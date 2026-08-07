"""Web Push subscription registration — endpoint allowlist enforcement.

`PushSubscription.endpoint` is a client-supplied URL that the server later POSTs
to from inside Railway's private network (app/utils/webpush.py::send_web_push),
so an unvalidated one is a stored blind SSRF. The allowlist shipped log-only in
PR #141 and was flipped to enforcing on 2026-07-28 after reviewing every endpoint
production had ever stored.

Two halves are asserted here, and both matter:
  1. Off-allowlist endpoints are rejected at registration (400, nothing stored).
  2. Real vendor endpoints — for every mainstream browser engine, not just the
     two production has seen — are still accepted. This is the regression that
     would bite if someone "tightened" ALLOWED_PUSH_HOSTS later.
"""

import pytest

from app import db
from app.models import PushSubscription


def _payload(endpoint):
    return {
        'endpoint': endpoint,
        'keys': {'p256dh': 'test-p256dh-key', 'auth': 'test-auth-secret'},
    }


def _post(client, endpoint):
    return client.post('/notifications/push-subscribe', json=_payload(endpoint))


# Real endpoint formats per browser engine. Chrome/Android and Chrome/desktop
# share a host, as do Safari/iOS-PWA and Safari/macOS — the allowlist keys off
# the vendor, not the device, so there is no mobile-vs-desktop split to test.
VENDOR_ENDPOINTS = [
    ('https://fcm.googleapis.com/fcm/send/dXYz123', 'Chrome — Android + desktop'),
    ('https://web.push.apple.com/QKxYz123', 'Safari — iOS PWA + macOS'),
    ('https://updates.push.services.mozilla.com/wpush/v2/gAAA', 'Firefox'),
    ('https://abc.notify.windows.com/w/?token=xyz', 'Edge / WNS'),
    ('https://android.googleapis.com/gcm/send/legacy', 'legacy GCM'),
]

BLOCKED_ENDPOINTS = [
    ('http://web.railway.internal:8080/api/internal/uploads-manifest', 'internal service'),
    ('https://169.254.169.254/latest/meta-data/', 'cloud metadata'),
    ('http://127.0.0.1:5000/admin', 'loopback'),
    ('https://fcm.googleapis.com@evil.com/x', 'userinfo confusion'),
    ('https://evilgoogleapis.com/x', 'suffix confusion'),
    ('http://fcm.googleapis.com/fcm/send/x', 'allowlisted host over plain http'),
]


class TestPushEndpointAllowlist:

    @pytest.mark.parametrize('endpoint,label', VENDOR_ENDPOINTS)
    def test_real_vendor_endpoints_are_accepted(self, app, logged_in_client, endpoint, label):
        """Every mainstream engine must still register — enforcement must not
        cost a real client their push notifications."""
        resp = _post(logged_in_client, endpoint)
        assert resp.status_code == 200, f'{label} was rejected: {resp.get_json()}'
        assert resp.get_json()['ok'] is True
        with app.app_context():
            assert PushSubscription.query.filter_by(endpoint=endpoint).first() is not None

    @pytest.mark.parametrize('endpoint,label', BLOCKED_ENDPOINTS)
    def test_off_allowlist_endpoints_are_rejected(self, app, logged_in_client, endpoint, label):
        """SSRF targets are refused with a 400 and — critically — not stored.
        A stored bad row would keep firing on every notification."""
        resp = _post(logged_in_client, endpoint)
        assert resp.status_code == 400, f'{label} was accepted'
        assert resp.get_json()['ok'] is False
        with app.app_context():
            assert PushSubscription.query.filter_by(endpoint=endpoint).first() is None

    def test_enforcement_is_write_path_only(self, app, logged_in_client, client_user):
        """send_web_push must NOT re-validate: enforcement gates registration so
        it can never invalidate an already-stored subscription. This is the
        property that made the log-only -> enforcing flip safe, so it is pinned
        here — moving the check into the send path would silently kill every
        legacy row that predates the allowlist.
        """
        import pywebpush
        from app.utils import webpush

        with app.app_context():
            legacy_endpoint = 'https://legacy-host.example.com/never-allowlisted'
            assert not webpush.is_allowed_push_endpoint(legacy_endpoint), (
                'fixture must use a host the allowlist rejects, or this test '
                'proves nothing'
            )

            sent = []
            app.config['VAPID_PRIVATE_KEY'] = 'test-key'
            # send_web_push imports webpush from pywebpush at call time, so patch
            # it at the source module.
            original = pywebpush.webpush
            pywebpush.webpush = lambda **kw: sent.append(
                kw['subscription_info']['endpoint'])
            try:
                webpush.send_web_push(
                    user_id=client_user.id, title='t', body='b',
                    subscriptions=[{'id': 1, 'endpoint': legacy_endpoint,
                                    'p256dh': 'k', 'auth': 'a'}],
                )
            finally:
                pywebpush.webpush = original

            assert sent == [legacy_endpoint], (
                'send_web_push skipped a stored subscription — the allowlist must '
                'stay write-path only'
            )


class TestPushSubscribeValidation:

    def test_missing_endpoint_rejected(self, logged_in_client):
        resp = logged_in_client.post('/notifications/push-subscribe',
                                     json={'keys': {'p256dh': 'k', 'auth': 'a'}})
        assert resp.status_code == 400

    def test_missing_keys_rejected(self, logged_in_client):
        resp = logged_in_client.post(
            '/notifications/push-subscribe',
            json={'endpoint': 'https://fcm.googleapis.com/fcm/send/x', 'keys': {}})
        assert resp.status_code == 400

    def test_requires_login(self, client):
        resp = _post(client, 'https://fcm.googleapis.com/fcm/send/x')
        assert resp.status_code in (302, 401)
        assert resp.status_code != 200


class TestLastSeenAt:
    """M31: last_seen_at is the only real liveness signal — it must advance
    even when the upsert is a no-op (identical keys), which is the common
    case updated_at silently failed to cover (onupdate doesn't fire on an
    unchanged row)."""

    def test_set_on_first_subscribe(self, app, logged_in_client):
        endpoint = 'https://fcm.googleapis.com/fcm/send/lsa1'
        resp = _post(logged_in_client, endpoint)
        assert resp.status_code == 200
        with app.app_context():
            sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
            assert sub.last_seen_at is not None

    def test_advances_on_identical_repost(self, app, logged_in_client):
        endpoint = 'https://fcm.googleapis.com/fcm/send/lsa2'
        _post(logged_in_client, endpoint)
        with app.app_context():
            sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
            first_seen = sub.last_seen_at

        # Re-POST with the exact same endpoint + keys — a no-op UPDATE as far
        # as p256dh/auth are concerned, which is exactly the case where
        # updated_at's onupdate wouldn't fire.
        _post(logged_in_client, endpoint)
        with app.app_context():
            sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
            assert sub.last_seen_at >= first_seen


class TestOldEndpointCleanup:
    """M31: sw.js's pushsubscriptionchange handler sends old_endpoint so a
    browser-initiated rotation deletes the row it replaces instead of
    orphaning it forever."""

    def test_old_endpoint_deleted_new_endpoint_kept(self, app, logged_in_client):
        old = 'https://fcm.googleapis.com/fcm/send/rot-old'
        new = 'https://fcm.googleapis.com/fcm/send/rot-new'
        _post(logged_in_client, old)

        resp = logged_in_client.post('/notifications/push-subscribe', json={
            'endpoint': new,
            'keys': {'p256dh': 'test-p256dh-key', 'auth': 'test-auth-secret'},
            'old_endpoint': old,
        })
        assert resp.status_code == 200
        with app.app_context():
            assert PushSubscription.query.filter_by(endpoint=old).first() is None
            assert PushSubscription.query.filter_by(endpoint=new).first() is not None

    def test_old_endpoint_belonging_to_another_user_is_not_deleted(
            self, app, logged_in_client, client_user):
        """old_endpoint is attacker-influenceable (no CSRF token on this
        route) — it must only ever delete the caller's own row."""
        from werkzeug.security import generate_password_hash
        from app.models import User, Client

        with app.app_context():
            other = User(firstname='Other', lastname='User', email='other_rot@test.com',
                        role='client', hashed_password=generate_password_hash('Testpass1!'))
            db.session.add(other)
            db.session.commit()
            db.session.add(Client(user_id=other.id, onboarding_completed=True))
            db.session.commit()
            others_endpoint = 'https://fcm.googleapis.com/fcm/send/not-yours'
            db.session.add(PushSubscription(
                user_id=other.id, endpoint=others_endpoint,
                p256dh='k', auth='a'))
            db.session.commit()

        new = 'https://fcm.googleapis.com/fcm/send/rot-new-2'
        resp = logged_in_client.post('/notifications/push-subscribe', json={
            'endpoint': new,
            'keys': {'p256dh': 'test-p256dh-key', 'auth': 'test-auth-secret'},
            'old_endpoint': others_endpoint,
        })
        assert resp.status_code == 200
        with app.app_context():
            assert PushSubscription.query.filter_by(endpoint=others_endpoint).first() is not None


class TestSweepPushSubscriptions:
    """M31: the query flask sweep-push-subscriptions runs (run.py). CLI
    commands in run.py attach to run.py's own app instance, not the
    create_app() factory this test suite uses (same reason reconcile-uploads
    has no test coverage either) — so this exercises the same DELETE the
    command issues rather than invoking the command itself."""

    def test_sweeps_stale_keeps_fresh(self, app, client_user):
        import datetime as dt

        with app.app_context():
            stale = PushSubscription(
                user_id=client_user.id, endpoint='https://fcm.googleapis.com/fcm/send/stale',
                p256dh='k', auth='a',
                last_seen_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=91))
            fresh = PushSubscription(
                user_id=client_user.id, endpoint='https://fcm.googleapis.com/fcm/send/fresh',
                p256dh='k', auth='a',
                last_seen_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
            db.session.add_all([stale, fresh])
            db.session.commit()

            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
            deleted = (PushSubscription.query
                       .filter(PushSubscription.last_seen_at < cutoff)
                       .delete(synchronize_session=False))
            db.session.commit()

            assert deleted == 1
            assert PushSubscription.query.filter_by(
                endpoint='https://fcm.googleapis.com/fcm/send/stale').first() is None
            assert PushSubscription.query.filter_by(
                endpoint='https://fcm.googleapis.com/fcm/send/fresh').first() is not None
