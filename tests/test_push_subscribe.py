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
