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


# ── Grouped notifications: one text source + a per-request batcher ─────────────
# NOTIFICATIONS.md §9.3/§9.4 (Session 2). summarise() is the single source of
# booking-notification text — used for both the bell and (later, Session 4) the
# activity feed, so wording is identical regardless of who triggered the event.

# Event "kind" → notification_type styling key (§2). 'booking_waitlisted' is a
# flavour of 'booking_requested' for styling purposes.
_KIND_NTYPE = {
    'booking_confirmed':  'booking_confirmed',
    'booking_requested':  'booking_requested',
    'booking_waitlisted': 'booking_requested',
    'booking_cancelled':  'booking_cancelled',
    'walker_assigned':    'walker_assigned',
}

# Actor-prefixed verb per kind ("Lydia booked …", "John cancelled …").
_KIND_VERB = {
    'booking_confirmed':  'booked',
    'booking_requested':  'requested',
    'booking_waitlisted': 'requested',
    'booking_cancelled':  'cancelled',
}


def _fmt_day(d):
    """'Mon 1 Jun' — platform %-d (no leading zero)."""
    return d.strftime('%a %-d %b')


def _fmt_day_long(d):
    """'1 Jun 2026' — walker-facing date."""
    return d.strftime('%-d %b %Y')


def _fmt_range(dates):
    """Compact span: 'Mon 1 Jun' for one day, else 'Mon 1 – Fri 5 Jun'
    (month dropped on the low end when both ends share a month/year)."""
    lo, hi = min(dates), max(dates)
    if lo == hi:
        return _fmt_day(lo)
    if (lo.month, lo.year) == (hi.month, hi.year):
        return f"{lo.strftime('%a %-d')} – {hi.strftime('%a %-d %b')}"
    return f"{_fmt_day(lo)} – {_fmt_day(hi)}"


def _plural(label, n):
    """'walk'→'walks', 'drop-in'→'drop-ins'."""
    return label if n == 1 else label + 's'


def summarise(kind, payloads, *, actor_first=None):
    """Single source of booking-notification text (NOTIFICATIONS.md §9.4).

    kind:     one of _KIND_NTYPE.
    payloads: list of per-booking dicts. Recognised keys:
                dog_name    (str)              — required
                slot        ('Morning'/...)    — single-item wording
                date        (datetime.date)    — required
                svc_label   ('walk'/'drop-in') — default 'walk'
                walker_name (str|None)         — confirmed: 'Booked with X.'
                reason      (str|None)         — cancelled: e.g. closure reason
    actor_first: when set, frames as '<actor> booked/requested/cancelled …'
                 (admin-on-behalf, or fan-out to admins/co-owners). When None,
                 uses reflexive owner wording ('… confirmed').

    Returns (title, body, notification_type, link). The link is a sensible
    default; callers may override it per recipient via NotificationBatch.add.
    """
    if not payloads:
        raise ValueError("summarise() needs at least one payload")

    ntype = _KIND_NTYPE[kind]
    n = len(payloads)
    dates = [p['date'] for p in payloads]
    dog_names = sorted({p['dog_name'] for p in payloads})
    one_dog = dog_names[0] if len(dog_names) == 1 else None
    svc_labels = {p.get('svc_label', 'walk') for p in payloads}
    svc = svc_labels.pop() if len(svc_labels) == 1 else 'walk'

    # ── Walker assignment — its own template, ignores actor_first ─────────────
    if kind == 'walker_assigned':
        if n == 1:
            p = payloads[0]
            title = f"You have been assigned a {svc} on {_fmt_day_long(p['date'])}"
            body  = f"{p['dog_name']} — {p['slot']}"
            link  = f"/walker/pickups?date={p['date'].isoformat()}"
        else:
            title = f"You have been assigned {n} {_plural(svc, n)} ({_fmt_range(dates)})"
            body  = ', '.join(dog_names)
            link  = '/walker/schedule'
        return title, body, ntype, link

    # ── Booking events ───────────────────────────────────────────────────────
    dog_pfx = f"{one_dog}'s " if one_dog else ""

    if n == 1:
        p = payloads[0]
        slot_lower = (p.get('slot') or '').lower()
        when = _fmt_day(p['date'])
        if actor_first:
            verb = _KIND_VERB[kind]
            title = f"{actor_first} {verb} {dog_pfx}{slot_lower} {svc} on {when}"
            if kind == 'booking_confirmed':
                w = p.get('walker_name')
                body = f"Booked with {w}." if w else "Walker assigned."
            elif kind == 'booking_waitlisted':
                body = "On the waitlist — we'll let you know when a spot opens up."
            elif kind == 'booking_cancelled':
                body = p.get('reason')
            else:
                body = None
        else:
            if kind == 'booking_confirmed':
                title = f"{dog_pfx}{slot_lower} {svc} on {when} confirmed"
                w = p.get('walker_name')
                body = f"Booked with {w}." if w else "Walker assigned."
            elif kind == 'booking_requested':
                title = f"{dog_pfx}{slot_lower} {svc} on {when} requested"
                body = "We'll confirm shortly."
            elif kind == 'booking_waitlisted':
                title = f"{dog_pfx}{slot_lower} {svc} on {when} is on the waitlist"
                body = "We'll let you know when a spot opens up."
            else:  # booking_cancelled
                title = f"{dog_pfx}{slot_lower} {svc} on {when} cancelled"
                body = p.get('reason')
        return title, body, ntype, '/'

    # ── Grouped (N booking rows) ─────────────────────────────────────────────
    span = _fmt_range(dates)
    things = f"{n} {_plural(svc, n)}"
    multi_dog_suffix = '' if one_dog else f" · {', '.join(dog_names)}"

    if actor_first:
        verb = _KIND_VERB[kind]
        title = f"{actor_first} {verb} {dog_pfx}{things} ({span})"
    else:
        word = {
            'booking_confirmed':  'confirmed',
            'booking_requested':  'requested',
            'booking_waitlisted': 'waitlisted',
            'booking_cancelled':  'cancelled',
        }[kind]
        title = f"{dog_pfx}{things} {word} ({span})"

    if kind == 'booking_confirmed':
        body = f"{n} {_plural(svc, n)} booked.{multi_dog_suffix}".rstrip()
    elif kind == 'booking_cancelled':
        reasons = {p.get('reason') for p in payloads if p.get('reason')}
        if len(reasons) == 1:
            body = reasons.pop()
        else:
            body = f"{n} booking{'s' if n != 1 else ''} cancelled.{multi_dog_suffix}".rstrip()
    else:  # requested / waitlisted
        body = "We'll confirm shortly." if not multi_dog_suffix else f"{n} bookings.{multi_dog_suffix}".rstrip()

    return title, body, ntype, '/'


class NotificationBatch:
    """Collect per-recipient notification intents during one request, then emit
    ONE grouped notification per (recipient_id, kind) on flush() (§9.3).

    Every bulk path routes through this so client- and admin-initiated actions
    group identically. Like create_notification(), flush() does NOT commit —
    the caller still commits.

    Usage:
        batch = NotificationBatch(actor_id=current_user.id)
        for b in bookings:
            batch.add(b.user_id, 'booking_confirmed',
                      dog_name=dog.name, slot=b.slot, date=b.date,
                      walker_name=walker_first)
        batch.flush()
    """

    def __init__(self, actor_id):
        self.actor_id = actor_id
        self._groups = {}   # (recipient_id, kind) -> {actor_first, link, payloads}

    def add(self, recipient_id, kind, *, actor_first=None, link=None, **payload):
        """Queue one booking-event for a recipient. Repeated calls with the same
        (recipient_id, kind) accumulate into one grouped notification. The first
        add for a group fixes its actor_first / link override."""
        if kind not in _KIND_NTYPE:
            raise ValueError(f"unknown notification kind: {kind!r}")
        key = (recipient_id, kind)
        grp = self._groups.get(key)
        if grp is None:
            grp = {'actor_first': actor_first, 'link': link, 'payloads': []}
            self._groups[key] = grp
        grp['payloads'].append(payload)
        return self

    def flush(self):
        """Emit one create_notification per group (in add order). Returns the
        list of Notification rows. Clears the batch."""
        notifs = []
        for (recipient_id, kind), grp in self._groups.items():
            title, body, ntype, default_link = summarise(
                kind, grp['payloads'], actor_first=grp['actor_first']
            )
            notifs.append(create_notification(
                recipient_id=recipient_id,
                notification_type=ntype,
                title=title,
                body=body,
                link=grp['link'] or default_link,
                sender_id=self.actor_id,
            ))
        self._groups.clear()
        return notifs
