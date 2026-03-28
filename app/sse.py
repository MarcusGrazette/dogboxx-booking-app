"""Server-Sent Events (SSE) connection registry.

Maintains an in-memory map of user_id → active SSE queues.
Broadcasts events to all connected surfaces for a given user simultaneously.

⚠️  In-memory only — works with a single gunicorn worker (multiple threads).
    For multi-worker deployments, replace with a Redis pub/sub backend.
    Dev server: flask run (threaded by default) works out of the box.
"""

import threading
import queue
import json
import logging
from collections import defaultdict

log = logging.getLogger(__name__)

_lock = threading.Lock()
_connections: dict = defaultdict(list)   # user_id → [Queue, ...]


def subscribe(user_id: int) -> queue.Queue:
    """Register a new SSE connection for a user. Returns a fresh Queue."""
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

    Returns the number of connections notified.
    Safe to call from any thread.
    """
    payload = json.dumps(data)
    msg = f"event: {event_type}\ndata: {payload}\n\n"
    with _lock:
        queues = list(_connections.get(user_id, []))
    sent = 0
    for q in queues:
        try:
            q.put_nowait(msg)
            sent += 1
        except queue.Full:
            log.warning("SSE: queue full for user %s — dropping event '%s'", user_id, event_type)
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
