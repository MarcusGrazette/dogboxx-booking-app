"""
Weekly schedule — shared data layer for the walker "Weekly overview" tab and
the admin "Weekly Overview" roster page (app/blueprints/admin/views/weekly_overview.py).

Single source of truth so those two surfaces (and the admin copy-to-clipboard
text) can never disagree about who's working which day. Grouping logic here
mirrors app/blueprints/walker/routes.py's original _build_daily_overview,
just generalised to a date range and reused two ways: grouped by day (for the
walker week view) and grouped by walker (for the admin roster + copy text).

DogBoxx only operates Monday-Friday — every consumer of this module works in
terms of WEEKDAYS (5) days starting from get_week_start(), never a full
Mon-Sun week. Saturday/Sunday bookings should never exist, so there's no
value in fetching or grouping them.
"""
from datetime import timedelta

from sqlalchemy.orm import joinedload

from app.models import Booking, Walker
from app.utils.pricing import is_drop_in

WEEKDAYS = 5
WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']

# Stable per-walker color, keyed by walker_id % len(palette) — shared so a
# walker shows the same avatar color everywhere (assignment board, weekly
# overview), not a different one derived per page.
WALKER_COLORS = [
    '#8b5cf6',  # violet
    '#ec4899',  # pink
    '#f97316',  # orange
    '#14b8a6',  # teal
    '#3b82f6',  # blue
    '#a855f7',  # purple
    '#10b981',  # emerald
    '#f59e0b',  # amber
    '#6366f1',  # indigo
    '#84cc16',  # lime
]


def walker_color(walker_id):
    return WALKER_COLORS[walker_id % len(WALKER_COLORS)]


def walker_initials(walker):
    first = (walker.user.firstname or '')[:1].upper()
    last = (walker.user.lastname or '')[:1].upper()
    return (first + last) if last else first


def get_week_start(d):
    """Return the Monday on/before d. Matches WalkerSchedule.day_of_week's
    0=Monday convention — no isocalendar() needed, we just want "the Monday
    of this week", not an ISO year/week number."""
    return d - timedelta(days=d.weekday())


def fetch_week_bookings(week_start):
    """One query for every walker-assigned booking in the Mon-Fri window
    starting at week_start (a Monday). Callers group the result by day or by
    walker as needed — see build_week_by_day / build_week_by_walker."""
    week_end = week_start + timedelta(days=WEEKDAYS - 1)  # Friday
    return (
        Booking.query
        .options(
            joinedload(Booking.dog),
            joinedload(Booking.walker).joinedload(Walker.user),
            joinedload(Booking.service_type),
        )
        .filter(
            Booking.date >= week_start,
            Booking.date <= week_end,
            Booking.status.in_(Booking.WALKER_STATUSES),
            Booking.walker_id.isnot(None),
        )
        .order_by(Booking.date, Booking.slot, Booking.pickup_order)
        .all()
    )


def group_bookings_by_slot(bookings):
    """Group one day's bookings into {slot: {'walker_groups': [...], 'dog_count': N}}.

    Each walker group is {'walker': Walker, 'is_drop_in': bool, 'bookings': [Booking, ...]}.
    Drop-in and group-walk assignments for the same walker appear as separate
    groups so a DROP-IN badge (rendered by the caller) is unambiguous. Walkers
    with no bookings in a slot are omitted.
    """
    overview = {}
    for slot in ('Morning', 'Afternoon'):
        slot_bookings = [b for b in bookings if b.slot == slot]

        groups = {}
        for b in slot_bookings:
            key = (b.walker_id, is_drop_in(b))
            groups.setdefault(key, {
                'walker': b.walker,
                'is_drop_in': is_drop_in(b),
                'bookings': [],
            })['bookings'].append(b)

        # Sort: group walks first, drop-ins after; within each, walker first name
        walker_groups = sorted(
            groups.values(),
            key=lambda g: (g['is_drop_in'], (g['walker'].user.firstname or '').lower()),
        )

        overview[slot] = {
            'walker_groups': walker_groups,
            'dog_count': sum(len(g['bookings']) for g in walker_groups),
        }
    return overview


def build_week_by_day(bookings, week_start):
    """{date: {slot: {'walker_groups': [...], 'dog_count': N}}} for the 5
    weekdays starting at week_start. Feeds the walker week view."""
    by_day = {}
    for i in range(WEEKDAYS):
        d = week_start + timedelta(days=i)
        day_bookings = [b for b in bookings if b.date == d]
        by_day[d] = group_bookings_by_slot(day_bookings)
    return by_day


def build_week_by_walker(bookings):
    """List of {'walker': Walker, 'days': {date: [Booking, ...]}}, one entry
    per walker with >=1 booking in the week, sorted by first name. Feeds the
    admin roster + format_walker_week_text."""
    by_walker = {}
    for b in bookings:
        entry = by_walker.setdefault(b.walker_id, {'walker': b.walker, 'days': {}})
        entry['days'].setdefault(b.date, []).append(b)

    return sorted(
        by_walker.values(),
        key=lambda w: (w['walker'].user.firstname or '').lower() if w['walker'].user else '',
    )


def day_walker_names(day_data):
    """[(abbrev, 'Aaron, Sarah'), ...] for one day's {slot: {'walker_groups':
    [...]}} data (see group_bookings_by_slot) — Morning then Afternoon,
    skipping slots nobody's working. Names are deduped and sorted (a walker
    doing both a walk and a drop-in in the same slot appears once). Feeds
    the admin roster's "who's working, by day" summary card."""
    parts = []
    for slot, abbrev in (('Morning', 'AM'), ('Afternoon', 'PM')):
        names = sorted({
            g['walker'].user.firstname
            for g in day_data[slot]['walker_groups']
            if g['walker'] and g['walker'].user
        })
        if names:
            parts.append((abbrev, ', '.join(names)))
    return parts


def format_roster_week_text(week_by_day, week_start):
    """Canonical clipboard text for the whole team's week — the Copy button
    on the admin roster's top "who's working, by day" card, e.g.:

        Team schedule — week of 27 Jul
        Mon: AM Sarah, Priya · PM Priya
        Tue: AM Sarah
        Wed: —
        Thu: —
        Fri: —

    `week_by_day` is the {date: {slot: {...}}} shape from build_week_by_day.
    Mirrors format_walker_week_text's per-day formatting (day_walker_names
    stands in for that function's dog-name grouping) so the two texts read
    the same way.
    """
    lines = [f"Team schedule — week of {week_start.strftime('%-d %b')}"]

    for i in range(WEEKDAYS):
        d = week_start + timedelta(days=i)
        parts = day_walker_names(week_by_day[d])
        if not parts:
            lines.append(f"{WEEKDAY_LABELS[i]}: —")
        else:
            lines.append(f"{WEEKDAY_LABELS[i]}: " + " · ".join(f"{abbrev} {names}" for abbrev, names in parts))

    return "\n".join(lines)


def day_slot_parts(day_bookings):
    """[(abbrev, 'Bella, Max'), ...] for one day's bookings, Morning then
    Afternoon, skipping slots with nothing booked. Shared building block for
    both format_walker_week_text (clipboard text) and the admin weekly
    roster's on-screen day-by-day list — one place derives "what a slot
    looks like as text" so the two can't drift apart."""
    slot_dogs = {}
    for b in day_bookings:
        slot_dogs.setdefault(b.slot, []).append(b.dog.name if b.dog else '—')

    parts = []
    for slot, abbrev in (('Morning', 'AM'), ('Afternoon', 'PM')):
        if slot in slot_dogs:
            parts.append((abbrev, ', '.join(slot_dogs[slot])))
    return parts


def format_walker_week_text(walker, days, week_start):
    """Canonical clipboard text for one walker's week, e.g.:

        Sarah — week of 28 Jul
        Mon: AM Bella, Max
        Tue: AM Rex · PM Luna
        Wed: —
        Thu: AM Bella
        Fri: PM Max, Rex, Luna

    `days` is the {date: [Booking, ...]} shape from build_week_by_walker.
    Always exactly WEEKDAYS+1 lines (header + Mon..Fri) regardless of input —
    Saturday/Sunday never appear. No drop-in flag in the text (dogs from a
    walk and a drop-in in the same slot are just listed together).
    """
    firstname = walker.user.firstname if walker.user else 'Walker'
    lines = [f"{firstname} — week of {week_start.strftime('%-d %b')}"]

    for i in range(WEEKDAYS):
        d = week_start + timedelta(days=i)
        day_bookings = days.get(d, [])

        parts = day_slot_parts(day_bookings)
        if not parts:
            lines.append(f"{WEEKDAY_LABELS[i]}: —")
        else:
            lines.append(f"{WEEKDAY_LABELS[i]}: " + " · ".join(f"{abbrev} {names}" for abbrev, names in parts))

    return "\n".join(lines)
