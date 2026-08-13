from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import render_template, request, jsonify, Response, stream_with_context, current_app, redirect, url_for
from flask_login import login_required, current_user
from . import notifications_bp
from app.utils.notifications import mark_read, mark_all_read, get_recent
from app.models import Notification, PushSubscription
from app.utils.notifications import get_meta
from app.utils.http import wants_json
from app import db, limiter, csrf


@notifications_bp.route('/stream')
@login_required
@limiter.exempt
def stream():
    """SSE endpoint — streams real-time notification events to the browser.

    Each open tab/PWA window opens one long-lived connection here.
    Events pushed: 'notification', 'read_one', 'read_all'.
    """
    from app.sse import subscribe, stream_generator
    user_id = current_user.id
    q = subscribe(user_id)
    if q is None:
        # At the per-user connection cap — the browser's EventSource treats
        # any non-2xx as a failure and already retries with backoff
        # (notification_bell.html's onerror handler), so no client change
        # needed. A capped-out surface just falls back to the bell's existing
        # foreground reconciliation instead of live push.
        return Response('', status=429, mimetype='text/event-stream')
    return Response(
        stream_with_context(stream_generator(user_id, q)),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # tell nginx not to buffer this stream
            'Connection': 'keep-alive',
        },
    )


@notifications_bp.route('/')
@login_required
def index():
    """Full notification list page — most recent NOTIF_PAGE_CAP entries."""
    from app.utils.notifications import NOTIF_PAGE_CAP
    notifications = (Notification.query
                     .filter_by(recipient_id=current_user.id)
                     .order_by(Notification.created_at.desc())
                     .limit(NOTIF_PAGE_CAP)
                     .all())
    return render_template('notifications/index.html',
                           notifications=notifications,
                           notification_meta=get_meta)


@notifications_bp.route('/unread-count')
@login_required
def unread_count():
    """AJAX: current unread count — used by the bell to reconcile the DOM
    badge and the PWA home-screen app badge when the app returns to the
    foreground (SSE events fired while iOS suspends the page are lost)."""
    from app.utils.notifications import get_unread_count
    return jsonify({'count': get_unread_count(current_user.id)})


@notifications_bp.route('/recent')
@login_required
def recent():
    """AJAX: the navbar bell's unread count + top-N notification rows.

    Companion to /unread-count: the badge has a server-truth reconciliation
    path (page load + visibilitychange) but the dropdown LIST previously did
    not — it was only built by the server on page render and by live SSE
    prepends. Any notification arriving while the page's EventSource is
    suspended (iOS backgrounding the PWA, lost SSE event) updated the badge on
    the next foreground reconcile but never entered the list, leaving it stale
    until a full reload. The bell now refetches this on visibilitychange to
    rebuild the list alongside the badge.

    Item shape matches the SSE 'notification' event so the client renders
    live-pushed and reconciled rows through one code path.
    """
    from app.utils.notifications import get_unread_count, get_recent
    notifs = get_recent(current_user.id)
    items = []
    for n in notifs:
        icon, colour = get_meta(n.notification_type)
        items.append({
            'id': n.id,
            'title': n.title,
            'body': n.body or '',
            'link': n.link or '',
            'icon': icon,
            'colour': colour,
            'created_at': n.created_at.strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
            'is_unread': n.is_unread,
        })
    return jsonify({
        'count': get_unread_count(current_user.id),
        'notifications': items,
    })


@notifications_bp.route('/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_one_read(notification_id):
    """AJAX: mark a single notification read. Returns JSON."""
    success = mark_read(notification_id, current_user.id)
    return jsonify({'ok': success})


@notifications_bp.route('/read-all', methods=['POST'])
@login_required
def mark_all():
    """Mark all notifications read for current user."""
    mark_all_read(current_user.id)
    # Support both AJAX and regular form POST
    if wants_json():
        return jsonify({'ok': True})
    return redirect(url_for('notifications.index'))


# ── Web Push subscription management ─────────────────────────────────────────

@notifications_bp.route('/push-subscribe', methods=['POST'])
@login_required
@limiter.limit("20 per hour")
@csrf.exempt
def push_subscribe():
    """Save (or refresh) a Web Push subscription for the current user.

    Expects JSON body:
        {
            "endpoint": "https://fcm.googleapis.com/...",
            "keys": {
                "p256dh": "<base64url>",
                "auth":   "<base64url>"
            },
            "old_endpoint": "https://fcm.googleapis.com/..."   // optional, M31
        }

    Upserts on endpoint — safe to call on every page load. `old_endpoint`,
    when present, is the subscription this one replaces (sent by sw.js's
    pushsubscriptionchange handler on a browser-initiated rotation) — deleted
    for the current user so the rotation doesn't orphan the old row forever.

    CSRF-exempt: the service worker's pushsubscriptionchange handler can fire
    with no page open and no access to a page-embedded CSRF token, so this
    can't require one. Compensating controls: @login_required (a cross-site
    POST carries no session cookie at all under SESSION_COOKIE_SAMESITE=Lax,
    which is the primary thing CSRF protection defends against here) plus the
    endpoint allowlist below, which bounds what this can actually be used for
    regardless.

    Returns 400 if the endpoint host isn't a known push service (SSRF guard —
    see the comment below). All five mainstream browser engines are allowlisted,
    so this should only ever fire on an attack or a genuinely new vendor.
    """
    data = request.get_json(silent=True)
    if not data or not data.get('endpoint') or not data.get('keys'):
        return jsonify({'ok': False, 'error': 'invalid payload'}), 400

    endpoint     = data['endpoint']
    old_endpoint = data.get('old_endpoint')
    p256dh       = data['keys'].get('p256dh', '')
    auth         = data['keys'].get('auth', '')

    if not p256dh or not auth:
        return jsonify({'ok': False, 'error': 'missing keys'}), 400

    # SSRF mitigation — ENFORCING since 2026-07-28 (was log-only from PR #141).
    # `endpoint` is a client-supplied URL that the server later POSTs to from
    # inside Railway's private network, so an unvalidated one is a stored blind
    # SSRF: internal hosts, cloud metadata, anything routable from `web`.
    #
    # Flipped after reviewing every endpoint ever stored in production
    # (Apr–Jul 2026, 26 subscriptions across 14 users): only fcm.googleapis.com
    # and web.push.apple.com, both already allowlisted, both https. Enforcement
    # is write-path only — is_allowed_push_endpoint() is deliberately NOT called
    # in send_web_push(), so existing rows keep delivering and flipping this
    # could not break a working subscription.
    #
    # Logged at error, not warning: a hit here is now an exception rather than
    # routine noise, so it should reach Sentry. The host alone is what you need
    # to extend ALLOWED_PUSH_HOSTS — the full endpoint is a delivery capability,
    # so it stays out of the logs.
    from app.utils.webpush import is_allowed_push_endpoint
    if not is_allowed_push_endpoint(endpoint):
        host = urlparse(endpoint or '').hostname
        current_app.logger.error(
            'Rejected push endpoint not on allowlist — user %s host=%r',
            current_user.id, host,
        )
        return jsonify({'ok': False, 'error': 'unsupported push endpoint'}), 400

    # Upsert: update if endpoint exists for this user, otherwise insert.
    # If the endpoint exists under a *different* user (e.g. shared browser,
    # or leaked endpoint URL), delete the old row before inserting — never
    # silently transfer ownership, since push endpoints are sensitive.
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub and sub.user_id != current_user.id:
        current_app.logger.warning(
            'Push subscription endpoint reassigned from user %s to user %s',
            sub.user_id, current_user.id,
        )
        db.session.delete(sub)
        db.session.flush()  # release unique constraint on endpoint before re-insert
        sub = None

    now = datetime.now(timezone.utc)
    if sub:
        sub.p256dh = p256dh
        sub.auth   = auth
        # Set explicitly rather than relying on updated_at's onupdate: an
        # identical-key upsert (the common case — most page loads re-POST the
        # same subscription) is a no-op UPDATE, so onupdate wouldn't fire even
        # though the device is still genuinely active (M31).
        sub.last_seen_at = now
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            last_seen_at=now,
        )
        db.session.add(sub)

    # Browser-initiated rotation (M31): the old endpoint is still valid at the
    # push service — it'll keep returning 201 forever, so send_web_push()'s
    # 404/410 pruning never catches it — but nothing renders since the
    # subscription behind it has moved to `endpoint`. Delete it now instead of
    # leaving it to the 90-day sweep. Scoped to the current user: this is
    # reachable without a CSRF token (see docstring), so it must not be able
    # to delete another user's row.
    if old_endpoint and old_endpoint != endpoint:
        PushSubscription.query.filter_by(
            user_id=current_user.id, endpoint=old_endpoint,
        ).delete(synchronize_session=False)

    db.session.commit()
    current_app.logger.info('Push subscription saved for user %s', current_user.id)
    return jsonify({'ok': True})


@notifications_bp.route('/push-subscribe', methods=['DELETE'])
@login_required
def push_unsubscribe():
    """Remove a Web Push subscription (user opted out or browser unsubscribed).

    Expects JSON body: { "endpoint": "https://..." }
    """
    data = request.get_json(silent=True)
    if not data or not data.get('endpoint'):
        return jsonify({'ok': False, 'error': 'missing endpoint'}), 400

    deleted = PushSubscription.query.filter_by(
        endpoint=data['endpoint'],
        user_id=current_user.id,
    ).delete()
    db.session.commit()

    return jsonify({'ok': True, 'deleted': deleted})
