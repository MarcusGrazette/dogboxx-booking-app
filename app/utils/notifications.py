"""
Notification helpers — create, fetch, and mark notifications.

Usage:
    from app.utils.notifications import create_notification, get_unread_count

    create_notification(
        recipient_id=client.user_id,
        notification_type='booking_confirmed',
        title='Your walk on Mon 10 Mar has been confirmed',
        body='Daisy is booked for the Morning slot with Alice.',
        link='/bookings/42',
        sender_id=current_user.id,
    )
"""

from datetime import datetime, timezone
from app import db
from app.models import Notification


# ── Type metadata ─────────────────────────────────────────────────────────────
# Maps notification_type → (Bootstrap icon class, CSS colour)
# Used in templates for icon + colour rendering.

NOTIFICATION_META = {
    'booking_confirmed':  ('bi-check-circle-fill',  '#198754'),   # green
    'booking_cancelled':  ('bi-x-circle-fill',       '#dc3545'),   # red
    'booking_requested':  ('bi-calendar-plus-fill',  '#E02FAC'),   # pink
    'walker_assigned':    ('bi-person-check-fill',   '#0d6efd'),   # blue
    'dental_confirmed':   ('bi-check-circle-fill',   '#198754'),   # green
    'dental_available':   ('bi-calendar-event-fill', '#E02FAC'),   # pink
    'system':             ('bi-info-circle-fill',    '#6c757d'),   # grey
}

DEFAULT_META = ('bi-bell-fill', '#6c757d')


def get_meta(notification_type):
    """Return (icon_class, colour) for a given notification type."""
    return NOTIFICATION_META.get(notification_type, DEFAULT_META)


# ── Core helpers ──────────────────────────────────────────────────────────────

def create_notification(recipient_id, notification_type, title,
                        body=None, link=None, sender_id=None):
    """
    Insert a notification row and flush to DB.
    Returns the new Notification instance.

    Also queues an SSE broadcast that fires after the caller commits,
    so all open browser tabs/PWA windows for this user update instantly.
    """
    notif = Notification(
        recipient_id=recipient_id,
        sender_id=sender_id,
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
    )
    db.session.add(notif)
    db.session.flush()   # get an ID without committing — caller commits

    # Queue SSE event — fires in the after_commit hook (app/__init__.py)
    icon, colour = get_meta(notification_type)
    pending = db.session.info.setdefault('sse_pending', [])
    pending.append({
        'user_id': recipient_id,
        'event': 'notification',
        'data': {
            'id': notif.id,
            'type': notification_type,
            'title': title,
            'body': body or '',
            'link': link or '',
            'icon': icon,
            'colour': colour,
            'created_at': notif.created_at.isoformat(),
        },
    })

    return notif


def get_unread_count(user_id):
    """Return count of unread notifications for a user. Safe to call frequently."""
    return Notification.query.filter_by(
        recipient_id=user_id,
        read_at=None,
    ).count()


def get_recent(user_id, limit=8):
    """Return the most recent notifications for a user (read + unread)."""
    return (Notification.query
            .filter_by(recipient_id=user_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .all())


def mark_read(notification_id, user_id):
    """Mark a single notification as read. Validates ownership. Returns bool."""
    notif = Notification.query.filter_by(
        id=notification_id,
        recipient_id=user_id,
    ).first()
    if notif and notif.read_at is None:
        notif.read_at = datetime.now(timezone.utc)
        db.session.commit()
        # Broadcast to all other open surfaces for this user
        from app.sse import broadcast
        broadcast(user_id, 'read_one', {'id': notification_id})
        return True
    return False


def mark_all_read(user_id):
    """Bulk-mark all unread notifications for a user as read."""
    now = datetime.now(timezone.utc)
    Notification.query.filter_by(
        recipient_id=user_id,
        read_at=None,
    ).update({'read_at': now})
    db.session.commit()
    # Broadcast to all other open surfaces for this user
    from app.sse import broadcast
    broadcast(user_id, 'read_all', {})
