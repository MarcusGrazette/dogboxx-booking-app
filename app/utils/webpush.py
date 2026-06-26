"""
Web Push delivery utility.

Sends a push notification to all active push subscriptions for a user.
Dead subscriptions (410 Gone / 404 Not Found from the push service) are
automatically removed from the database.

Usage (called from the after_commit hook — never call inside a transaction):

    from app.utils.webpush import send_web_push
    send_web_push(user_id, title="Walk confirmed", body="Daisy is booked.",
                  link="/bookings/42")
"""

import json
import logging
from urllib.parse import urlparse

from flask import current_app

log = logging.getLogger(__name__)


# ── Web Push endpoint allowlist (SSRF mitigation — see SECURITY_REVIEW.md #4) ──
#
# The push `endpoint` is a client-supplied URL that the server later makes an
# outbound POST to (`send_web_push` → `pywebpush.webpush`). Without validation an
# authenticated user can point it at internal/metadata hosts (blind SSRF). Real
# browsers only ever emit endpoints under a small set of vendor push zones, so we
# allowlist those. Leading-dot suffixes anchor on a label boundary, so
# `evilgoogleapis.com` cannot match `.googleapis.com`. VAPID signing does NOT
# mitigate this — it authenticates the message, not the request destination.
#
# NOTE: a few entries (`.googleapis.com`, `.apple.com`) are broader than the push
# zones proper; they stay safe because the parent domains are vendor-owned (an
# attacker can't register a subdomain to reach internal hosts). Tighten to the
# hosts the monitor logs actually show before flipping to enforcing (see #4).
ALLOWED_PUSH_HOSTS = (
    '.googleapis.com',             # Google FCM (Chrome / Android)
    '.fcm.googleapis.com',         # Modern FCM endpoints
    '.apple.com',                  # Apple APNs (Safari / iOS - broader to catch .push and .notify)
    '.notify.windows.com',         # Microsoft WNS (Edge)
    '.wns.windows.com',            # Microsoft WNS secondary
    '.push.services.mozilla.com',  # Mozilla (Firefox)
    '.push.opera.com',             # Opera
)


def is_allowed_push_endpoint(endpoint):
    """True if *endpoint* is an HTTPS URL on a known push-service host.

    Uses ``urlparse(...).hostname`` (not string matching) so userinfo tricks like
    ``https://fcm.googleapis.com@evil.com/`` resolve to the real host (``evil.com``)
    and are rejected. Trailing FQDN dots are normalised so a legitimate
    ``fcm.googleapis.com.`` is not wrongly rejected.
    """
    try:
        u = urlparse(endpoint or '')
    except (ValueError, AttributeError):
        return False
    if u.scheme != 'https':
        return False
    host = (u.hostname or '').lower().rstrip('.')
    if not host:
        return False
    return host.endswith(ALLOWED_PUSH_HOSTS)


def send_web_push(user_id, title, body='', link='/', icon=None, unread_count=1, subscriptions=None):
    """Send a Web Push notification to every registered device for *user_id*.

    *subscriptions* should be a list of dicts with keys: id, endpoint, p256dh, auth.
    These are pre-fetched by create_notification() while the DB session is still
    active, so this function never needs to query the DB itself (safe to call from
    an after_commit hook where the session is in committed state).

    Stale subscriptions (410/404 from push service) are cleaned up via a fresh
    DB connection to avoid the committed-session restriction.

    Silently skips if VAPID keys are not configured (e.g. in tests).
    """
    from pywebpush import webpush, WebPushException

    vapid_private = current_app.config.get('VAPID_PRIVATE_KEY', '')
    vapid_email   = current_app.config.get('VAPID_CLAIMS_EMAIL', '')

    if not vapid_private:
        log.debug('Web Push: VAPID_PRIVATE_KEY not set — skipping')
        return

    if not subscriptions:
        return

    base_url = current_app.config.get('APP_BASE_URL', '').rstrip('/')

    payload = json.dumps({
        'title':        title,
        'body':         body,
        'link':         link,
        'icon':         icon or f'{base_url}/static/android-chrome-192x192.png',
        'badge':        f'{base_url}/static/badge-mono.png',
        'tag':          'dogboxx-notification',
        'unread_count': unread_count,
    })

    stale_ids = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    'endpoint': sub['endpoint'],
                    'keys': {
                        'p256dh': sub['p256dh'],
                        'auth':   sub['auth'],
                    },
                },
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims={
                    'sub': f'mailto:{vapid_email}',
                },
            )
            log.debug('Web Push sent to user %s (sub %s)', user_id, sub['id'])

        except WebPushException as e:
            status = getattr(e.response, 'status_code', None) if e.response else None
            # pywebpush sometimes returns no response object even when the push
            # service replied with 410 — fall back to parsing the message.
            if status is None and ('410' in str(e) or '404' in str(e)):
                status = 410 if '410' in str(e) else 404
            if status in (404, 410):
                log.info('Web Push: stale subscription %s for user %s (HTTP %s) — removing',
                         sub['id'], user_id, status)
                stale_ids.append(sub['id'])
            else:
                log.warning('Web Push failed for user %s sub %s: %s', user_id, sub['id'], e)

        except Exception as e:
            log.warning('Web Push unexpected error for user %s sub %s: %s', user_id, sub['id'], e)

    # Clean up stale subscriptions using a fresh connection (session is committed)
    if stale_ids:
        from app import db
        from app.models import PushSubscription
        try:
            with db.engine.begin() as conn:
                conn.execute(
                    PushSubscription.__table__.delete().where(
                        PushSubscription.id.in_(stale_ids)
                    )
                )
            log.info('Web Push: removed %d stale subscriptions', len(stale_ids))
        except Exception as e:
            log.warning('Web Push: failed to remove stale subscriptions: %s', e)
