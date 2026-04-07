from flask import render_template, request, jsonify, Response, stream_with_context, current_app
from flask_login import login_required, current_user
from . import notifications_bp
from app.utils.notifications import mark_read, mark_all_read, get_recent
from app.models import Notification, PushSubscription
from app.utils.notifications import get_meta
from app import db, limiter


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
    if _wants_json():
        return jsonify({'ok': True})
    from flask import redirect, url_for
    return redirect(url_for('notifications.index'))


def _wants_json():
    return (request.accept_mimetypes.best == 'application/json'
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest')


# ── Web Push subscription management ─────────────────────────────────────────

@notifications_bp.route('/push-subscribe', methods=['POST'])
@login_required
def push_subscribe():
    """Save (or refresh) a Web Push subscription for the current user.

    Expects JSON body:
        {
            "endpoint": "https://fcm.googleapis.com/...",
            "keys": {
                "p256dh": "<base64url>",
                "auth":   "<base64url>"
            }
        }

    Upserts on endpoint — safe to call on every page load.
    """
    data = request.get_json(silent=True)
    if not data or not data.get('endpoint') or not data.get('keys'):
        return jsonify({'ok': False, 'error': 'invalid payload'}), 400

    endpoint = data['endpoint']
    p256dh   = data['keys'].get('p256dh', '')
    auth     = data['keys'].get('auth', '')

    if not p256dh or not auth:
        return jsonify({'ok': False, 'error': 'missing keys'}), 400

    # Upsert: update if endpoint exists, otherwise insert
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.user_id  = current_user.id
        sub.p256dh   = p256dh
        sub.auth     = auth
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        db.session.add(sub)

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
