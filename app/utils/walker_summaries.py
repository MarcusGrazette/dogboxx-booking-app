"""Daily walker summaries — one bell + push notification per walker listing
the dogs they're walking on a given day, slot by slot.

Two runs per weekday, both London time:
  * 'preview' at 15:00 — the next working day (Friday's covers Monday)
  * 'morning' at 08:00 — today

Walkers with no confirmed walks that day get nothing. The summary is built
from the live confirmed bookings at send time, which is what makes it safe
for instant walker notifications to stop beyond WALKER_NOTICE_DAYS
(notifications.py::walker_notify_now): a change made weeks ahead is never
lost, it just shows up here instead.

Run by `flask send-walker-summaries` from the walker-summaries Railway cron
service. WalkerSummarySent makes a re-run a no-op per walker.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import db
from app.capacity import eligible_walker_clause
from app.models import Booking, DailyMessage, ServiceType, User, Walker, WalkerSummarySent
from app.utils.dates import LOCAL_TZ
from app.utils.notifications import create_notification
from app.utils.pricing import SLOT_ORDER
from app.utils.sanitize import rich_text_to_plain

logger = logging.getLogger(__name__)

PREVIEW_HOUR = 15   # London — previews the next working day
MORNING_HOUR = 8    # London — today

MESSAGE_MAX_CHARS = 200


def next_working_day(d):
    """The next Mon-Fri date after d (Friday → Monday)."""
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def due_summary(now_utc=None):
    """Which summary is due at this moment, as (kind, date), or None.

    The cron fires at both UTC hours that can be 08:00 / 15:00 London (one
    for GMT, one for BST); only the run whose London hour matches sends.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    local = now_utc.astimezone(LOCAL_TZ)
    if local.weekday() >= 5:
        return None
    if local.hour == MORNING_HOUR:
        return 'morning', local.date()
    if local.hour == PREVIEW_HOUR:
        return 'preview', next_working_day(local.date())
    return None


def _day_label(kind, d):
    """'today' / 'tomorrow' / 'on Monday', for the title."""
    if kind == 'morning':
        return 'today'
    sent_on = d - timedelta(days=1)
    if sent_on.weekday() < 5:
        return 'tomorrow'
    return f"on {d.strftime('%A')}"


def build_summary(kind, d, bookings, message_html=None):
    """(title, body, link) for one walker's summary of day d.

    Body groups dogs by slot in pickup order — walks first, then drop-ins,
    within each slot — and appends the day's admin announcement as plain
    text. One line with ' · ' separators: the bell renders the body as a
    single paragraph and push bodies are short.
    """
    groups = {}
    for b in bookings:
        is_drop_in = bool(b.service_type and b.service_type.slug == ServiceType.DROP_IN)
        groups.setdefault((b.slot, is_drop_in), []).append(b)

    parts = []
    for (slot, is_drop_in) in sorted(
            groups, key=lambda k: (SLOT_ORDER.get(k[0], len(SLOT_ORDER)), k[1])):
        rows = sorted(groups[(slot, is_drop_in)],
                      key=lambda b: (b.pickup_order is None, b.pickup_order or 0,
                                     b.dog.name if b.dog else ''))
        names = ', '.join(b.dog.name for b in rows if b.dog)
        label = f"{slot} drop-ins" if is_drop_in else slot
        parts.append(f"{label}: {names}")

    if message_html:
        note = rich_text_to_plain(message_html)
        if len(note) > MESSAGE_MAX_CHARS:
            note = note[:MESSAGE_MAX_CHARS - 1].rstrip() + '…'
        if note:
            parts.append(f"Note: {note}")

    title = f"Your walks {_day_label(kind, d)} ({d.strftime('%a %-d %b')})"
    return title, ' · '.join(parts), f"/walker/pickups/{d.isoformat()}"


def send_walker_summaries(kind, d):
    """Send the `kind` summary for day d to every eligible walker with at least
    one confirmed booking that day and no summary of this kind already sent.
    Commits once per walker so one failure can't block the rest. Returns the
    number of summaries sent."""
    if kind not in WalkerSummarySent.KINDS:
        raise ValueError(f"unknown summary kind: {kind!r}")

    bookings = (Booking.query
                .join(Walker, Booking.walker_id == Walker.id)
                .join(User, Walker.user_id == User.id)
                .filter(Booking.date == d,
                        Booking.status == 'confirmed',
                        eligible_walker_clause())
                .options(joinedload(Booking.dog), joinedload(Booking.service_type),
                         joinedload(Booking.walker))
                .all())
    by_walker = {}
    for b in bookings:
        by_walker.setdefault(b.walker_id, []).append(b)

    already_sent = {row.walker_id for row in WalkerSummarySent.query.filter_by(
        date=d, kind=kind)}
    message = DailyMessage.query.filter_by(date=d).first()
    message_html = message.content if message else None

    sent = 0
    for walker_id, rows in by_walker.items():
        if walker_id in already_sent:
            continue
        walker = rows[0].walker
        title, body, link = build_summary(kind, d, rows, message_html)
        try:
            db.session.add(WalkerSummarySent(walker_id=walker_id, date=d, kind=kind))
            create_notification(
                recipient_id=walker.user_id,
                notification_type='walker_summary',
                title=title,
                body=body,
                link=link,
            )
            db.session.commit()
            sent += 1
        except IntegrityError:
            # A concurrent run sent this one first — nothing to do.
            db.session.rollback()
        except Exception:
            db.session.rollback()
            logger.exception('Walker summary (%s, %s) failed for walker %s',
                             kind, d, walker_id)
    return sent
