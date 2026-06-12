"""SSE transport tests — in-memory fan-out and the Redis pub/sub mode.

The Redis path is tested with a fake client (no Redis server in CI):
publish routing, no-double-delivery on the publishing worker, fallback to
local delivery when Redis is down, and the listener's message handling.
"""
import pytest

from app import sse


@pytest.fixture(autouse=True)
def reset_sse_state():
    """Isolate module-level SSE state between tests."""
    sse._connections.clear()
    sse._publisher = None
    yield
    sse._connections.clear()
    sse._publisher = None


class FakeRedis:
    def __init__(self):
        self.published = []

    def publish(self, channel, msg):
        self.published.append((channel, msg))
        return 1   # one listener (worker) received it


class BrokenRedis:
    def publish(self, channel, msg):
        raise ConnectionError('redis down')


# ---------------------------------------------------------------------------
# In-memory fan-out (SSE_REDIS_URL unset — flask run / tests)
# ---------------------------------------------------------------------------

class TestInMemoryFanout:

    def test_broadcast_reaches_subscriber(self, app):
        with app.app_context():
            q = sse.subscribe(42)
            sent = sse.broadcast(42, 'notification', {'id': 1})
            assert sent == 1
            msg = q.get_nowait()
            assert msg.startswith('event: notification\n')
            assert '"id": 1' in msg
            sse.unsubscribe(42, q)

    def test_broadcast_without_subscribers_is_noop(self, app):
        with app.app_context():
            assert sse.broadcast(99, 'notification', {'id': 1}) == 0

    def test_all_surfaces_of_a_user_receive(self, app):
        with app.app_context():
            q1 = sse.subscribe(42)
            q2 = sse.subscribe(42)
            other = sse.subscribe(43)
            sent = sse.broadcast(42, 'read_all', {})
            assert sent == 2
            assert not q1.empty() and not q2.empty()
            assert other.empty()
            for q in (q1, q2):
                sse.unsubscribe(42, q)
            sse.unsubscribe(43, other)

    def test_unsubscribe_stops_delivery(self, app):
        with app.app_context():
            q = sse.subscribe(42)
            sse.unsubscribe(42, q)
            assert sse.broadcast(42, 'notification', {'id': 1}) == 0
            assert q.empty()


# ---------------------------------------------------------------------------
# Redis pub/sub transport (SSE_REDIS_URL set — prod, --workers > 1)
# ---------------------------------------------------------------------------

class TestRedisTransport:

    @pytest.fixture(autouse=True)
    def redis_mode(self, app, monkeypatch):
        monkeypatch.setitem(app.config, 'SSE_REDIS_URL', 'redis://fake:6379/0')
        # Never start the real listener thread in tests — it would retry
        # connecting to the fake URL forever.
        monkeypatch.setattr(sse, '_ensure_listener', lambda url: None)

    def test_broadcast_publishes_to_user_channel(self, app, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr(sse, '_get_publisher', lambda url: fake)
        with app.app_context():
            sent = sse.broadcast(7, 'read_one', {'id': 3})
        assert sent == 1
        channel, msg = fake.published[0]
        assert channel == 'sse:user:7'
        assert msg.startswith('event: read_one\n')
        assert '"id": 3' in msg

    def test_no_direct_local_delivery_in_redis_mode(self, app, monkeypatch):
        """The publishing worker's listener echoes the publish back — direct
        local delivery on top of that would double-send to same-worker
        connections."""
        fake = FakeRedis()
        monkeypatch.setattr(sse, '_get_publisher', lambda url: fake)
        with app.app_context():
            q = sse.subscribe(7)
            sse.broadcast(7, 'notification', {'id': 1})
            assert q.empty()
            sse.unsubscribe(7, q)

    def test_publish_failure_falls_back_to_local_delivery(self, app, monkeypatch):
        """Redis down → same-worker surfaces still get the event."""
        monkeypatch.setattr(sse, '_get_publisher', lambda url: BrokenRedis())
        with app.app_context():
            q = sse.subscribe(7)
            sent = sse.broadcast(7, 'notification', {'id': 1})
            assert sent == 1
            assert not q.empty()
            sse.unsubscribe(7, q)


# ---------------------------------------------------------------------------
# Listener message handling (what each worker does with a received publish)
# ---------------------------------------------------------------------------

class TestPubsubMessageHandling:

    def test_pmessage_fans_out_to_local_queues(self, app):
        with app.app_context():
            q = sse.subscribe(11)
        # redis-py delivers channel/data as bytes
        sse._handle_pubsub_message({
            'type': 'pmessage',
            'channel': b'sse:user:11',
            'data': b'event: notification\ndata: {"id": 5}\n\n',
        })
        msg = q.get_nowait()
        assert msg.startswith('event: notification\n')
        sse.unsubscribe(11, q)

    def test_non_pmessage_ignored(self, app):
        with app.app_context():
            q = sse.subscribe(11)
        sse._handle_pubsub_message({'type': 'psubscribe', 'channel': b'sse:user:*', 'data': 1})
        assert q.empty()
        sse.unsubscribe(11, q)

    def test_malformed_channel_ignored(self, app):
        with app.app_context():
            q = sse.subscribe(11)
        sse._handle_pubsub_message({
            'type': 'pmessage',
            'channel': b'sse:user:not-an-id',
            'data': b'event: x\ndata: {}\n\n',
        })
        assert q.empty()
        sse.unsubscribe(11, q)
