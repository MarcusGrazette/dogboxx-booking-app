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

# ── Caps ──────────────────────────────────────────────────────────────────────
NOTIF_DB_CAP   = 50   # max stored per user (oldest pruned at insert time)
NOTIF_PAGE_CAP = 20   # max shown on the full notifications page
NOTIF_BELL_CAP = 5    # max shown in the navbar bell dropdown


# ── Type metadata ─────────────────────────────────────────────────────────────
# Maps notification_type → (Bootstrap icon class, CSS colour)
# Used in templates for icon + colour rendering.

NOTIFICATION_META = {
    'booking_confirmed':    ('bi-check-circle-fill',  '#198754'),   # green
    'booking_cancelled':    ('bi-x-circle-fill',       '#dc3545'),   # red
    'booking_requested':    ('bi-calendar-plus-fill',  '#E02FAC'),   # pink
    'same_day_request':     ('bi-lightning-fill',      '#fd7e14'),   # orange — urgent same-day
    'walker_assigned':      ('bi-person-check-fill',   '#0d6efd'),   # blue
    'walker_availability':  ('bi-calendar-x-fill',     '#fd7e14'),   # orange
    'dental_confirmed':     ('bi-check-circle-fill',   '#198754'),   # green
    'dental_available':     ('bi-calendar-event-fill', '#E02FAC'),   # pink
    'system':               ('bi-info-circle-fill',    '#6c757d'),   # grey
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

    # Prune oldest notifications beyond the DB cap for this user
    oldest_ids = (
        db.session.query(Notification.id)
        .filter(Notification.recipient_id == recipient_id)
        .order_by(Notification.created_at.desc())
        .offset(NOTIF_DB_CAP)
        .all()
    )
    if oldest_ids:
        ids_to_delete = [row.id for row in oldest_ids]
        Notification.query.filter(Notification.id.in_(ids_to_delete)).delete(
            synchronize_session=False
        )

    # Queue SSE event — fires in the after_commit hook (app/__init__.py)
    icon, colour = get_meta(notification_type)
    event_data = {
        'id': notif.id,
        'type': notification_type,
        'title': title,
        'body': body or '',
        'link': link or '',
        'icon': icon,
        'colour': colour,
        'created_at': notif.created_at.strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
    }

    pending = db.session.info.setdefault('sse_pending', [])
    pending.append({
        'user_id': recipient_id,
        'event': 'notification',
        'data': event_data,
    })

    # Queue Web Push event — fires in the same after_commit hook.
    # Subscriptions and unread count are fetched NOW (session still active)
    # so send_web_push() doesn't need to query the DB from inside the hook.
    from app.models import PushSubscription
    subs = PushSubscription.query.filter_by(user_id=recipient_id).all()
    if subs:
        unread_count = Notification.query.filter_by(
            recipient_id=recipient_id,
            read_at=None,
        ).count()
        wp_pending = db.session.info.setdefault('webpush_pending', [])
        wp_pending.append({
            'user_id':       recipient_id,
            'title':         title,
            'body':          body or '',
            'link':          link or '/',
            'icon':          icon,
            'unread_count':  unread_count,
            'subscriptions': [
                {'id': s.id, 'endpoint': s.endpoint, 'p256dh': s.p256dh, 'auth': s.auth}
                for s in subs
            ],
        })

    return notif


def get_unread_count(user_id):
    """Return count of unread notifications for a user. Safe to call frequently."""
    return Notification.query.filter_by(
        recipient_id=user_id,
        read_at=None,
    ).count()


def get_recent(user_id, limit=NOTIF_BELL_CAP):
    """Return the most recent notifications for a user (read + unread).

    Defaults to NOTIF_BELL_CAP for the navbar bell dropdown.
    """
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
