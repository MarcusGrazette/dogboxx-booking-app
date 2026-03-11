from flask import render_template, request, jsonify, Response, stream_with_context
from flask_login import login_required, current_user
from . import notifications_bp
from app.utils.notifications import mark_read, mark_all_read, get_recent
from app.models import Notification
from app.utils.notifications import get_meta
from app import limiter


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
    """Full notification list page — paginated."""
    page = request.args.get('page', 1, type=int)
    notifications = (Notification.query
                     .filter_by(recipient_id=current_user.id)
                     .order_by(Notification.created_at.desc())
                     .paginate(page=page, per_page=20, error_out=False))
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
def mark_all(request=None):
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
