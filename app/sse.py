"""Server-Sent Events (SSE) connection registry.

Maintains an in-memory map of user_id → active SSE queues, and fans events
out to all connected surfaces for a given user.

Cross-worker transport: with SSE_REDIS_URL set (config.py — reads REDIS_URL,
the same instance the rate limiter uses), broadcast() publishes to a Redis
pub/sub channel instead of delivering directly. Each gunicorn worker runs one
listener (started lazily on first subscribe) that receives every publish and
fans out to its own local queues — so an event raised on worker A reaches an
SSE connection held by worker B. Pub/sub stores nothing in the Redis keyspace
and is at-most-once, matching the in-memory contract (missed events are
recovered by the bell's reconcile-on-foreground fetch).

Without SSE_REDIS_URL, delivery is in-memory only: correct for a single
process (flask run, tests), silently lossy under gunicorn --workers > 1.
"""

import threading
import queue
import json
import logging
import time
from collections import defaultdict

log = logging.getLogger(__name__)

_lock = threading.Lock()
_connections: dict = defaultdict(list)   # user_id → [Queue, ...]

# ── Redis transport state (lazy — only touched when SSE_REDIS_URL is set) ────
_CHANNEL_PREFIX = 'sse:user:'
_publisher = None            # shared redis client for PUBLISH (thread-safe pool)
_listener_started = False    # one listener thread per worker process


def _redis_url():
    """SSE_REDIS_URL from app config, or None outside an app context."""
    try:
        from flask import current_app
        return current_app.config.get('SSE_REDIS_URL')
    except RuntimeError:
        return None


def _get_publisher(url):
    """Lazily create the shared publish client (redis-py pools internally)."""
    global _publisher
    if _publisher is None:
        import redis as redis_lib
        _publisher = redis_lib.Redis.from_url(url)
    return _publisher


def _ensure_listener(url):
    """Start this worker's pub/sub listener thread once (idempotent).

    Under gunicorn's gevent worker, threading is monkey-patched so this is a
    greenlet and redis-py's blocking listen() yields cooperatively.
    """
    global _listener_started
    with _lock:
        if _listener_started:
            return
        _listener_started = True
    t = threading.Thread(target=_listen_loop, args=(url,), daemon=True,
                         name='sse-redis-listener')
    t.start()


def _listen_loop(url):
    """Receive publishes from all workers and fan out to local queues.

    Outer loop reconnects if Redis restarts; events fired while disconnected
    are lost (pub/sub has no replay) — the bell's reconciliation covers that.
    """
    import redis as redis_lib
    while True:
        try:
            client = redis_lib.Redis.from_url(url)
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            pubsub.psubscribe(_CHANNEL_PREFIX + '*')
            log.info('SSE: Redis listener subscribed to %s*', _CHANNEL_PREFIX)
            for message in pubsub.listen():
                _handle_pubsub_message(message)
        except Exception as e:
            log.warning('SSE: Redis listener error (%s) — reconnecting in 2s', e)
            time.sleep(2)


def _handle_pubsub_message(message):
    """Parse one pub/sub message and fan out to this worker's local queues."""
    if message.get('type') != 'pmessage':
        return
    channel = message['channel']
    if isinstance(channel, bytes):
        channel = channel.decode()
    try:
        user_id = int(channel[len(_CHANNEL_PREFIX):])
    except ValueError:
        return
    data = message['data']
    if isinstance(data, bytes):
        data = data.decode()
    _deliver_local(user_id, data)


def subscribe(user_id: int) -> queue.Queue:
    """Register a new SSE connection for a user. Returns a fresh Queue."""
    url = _redis_url()
    if url:
        _ensure_listener(url)
    q = queue.Queue(maxsize=50)
    with _lock:
        _connections[user_id].append(q)
    log.debug("SSE: user %s connected (%d active)", user_id, len(_connections[user_id]))
    return q


def unsubscribe(user_id: int, q: queue.Queue) -> None:
    """Remove a SSE connection queue for a user."""
    with _lock:
        try:
            _connections[user_id].remove(q)
            if not _connections[user_id]:
                del _connections[user_id]
        except (ValueError, KeyError):
            pass
    log.debug("SSE: user %s disconnected", user_id)


def broadcast(user_id: int, event_type: str, data: dict) -> int:
    """Push an event to all active SSE connections for a user.

    Redis mode: returns the number of worker listeners that received the
    publish (not end-user queues — local fan-out happens in each listener).
    In-memory mode: returns the number of local queues notified.
    Safe to call from any thread.
    """
    payload = json.dumps(data)
    msg = f"event: {event_type}\ndata: {payload}\n\n"

    url = _redis_url()
    if url:
        try:
            # Our own worker's listener receives this too — no local delivery
            # here, or same-worker connections would get the event twice.
            return _get_publisher(url).publish(_CHANNEL_PREFIX + str(user_id), msg)
        except Exception as e:
            log.warning('SSE: Redis publish failed (%s) — falling back to local delivery', e)

    return _deliver_local(user_id, msg)


def _deliver_local(user_id: int, msg: str) -> int:
    """Fan a pre-formatted SSE message out to this process's queues."""
    with _lock:
        queues = list(_connections.get(user_id, []))
    sent = 0
    for q in queues:
        try:
            q.put_nowait(msg)
            sent += 1
        except queue.Full:
            log.warning("SSE: queue full for user %s — dropping event", user_id)
    return sent


def stream_generator(user_id: int, q: queue.Queue):
    """Generator yielding SSE-formatted strings from a user's queue.

    Sends an immediate flush comment on connect so that reverse proxies
    (e.g. Tailscale Serve) don't close the connection before the first
    real ping arrives.  Keepalive pings fire every 15 s thereafter.
    Cleans up the queue registration when the client disconnects.
    """
    try:
        # Flush the HTTP headers immediately — this is critical for proxies
        # that buffer responses until they see data.  Without this, Tailscale
        # Serve closes the connection in ~2 s because nothing has been sent.
        yield ": connected\n\n"

        while True:
            try:
                msg = q.get(timeout=15)
                yield msg
            except queue.Empty:
                # SSE comment line — keeps the connection alive, ignored by clients
                yield ": ping\n\n"
    finally:
        unsubscribe(user_id, q)
