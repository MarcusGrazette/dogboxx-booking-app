"""
Weekly schedule — shared data layer for the walker "Weekly overview" tab and
the admin "Week view" page (app/blueprints/admin/views/weekly_overview.py).

Single source of truth so those two surfaces (and the admin copy-to-clipboard
text) can never disagree about who's working which day. Grouping logic here
mirrors app/blueprints/walker/routes.py's original _build_daily_overview,
just generalised to a date range and reused two ways: grouped by day (for the
walker week view) and grouped by walker (for the admin Week view's columns +
copy text).

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




def fetch_week_active_bookings(week_start):
    """Every active booking (confirmed, requested or waitlisted — any service)
    in the Mon-Fri window starting at week_start. Feeds the admin Week view
    grid (build_week_grid), which needs unassigned bookings too, for its
    "N unassigned" counts."""
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
            Booking.status.in_(Booking.ACTIVE_STATUSES),
        )
        .all()
    )


# Week view rows within each day, top to bottom: (slot, is_drop_in, label).
WEEK_GRID_ROWS = (
    ('Morning', True, 'Drop-ins'),
    ('Morning', False, 'Walks'),
    ('Afternoon', False, 'Walks'),
    ('Afternoon', True, 'Drop-ins'),
)


def build_week_grid(bookings, week_start, walker_ids):
    """Rows for the admin Week view: one entry per weekday, each with the
    WEEK_GRID_ROWS rows that apply. `bookings` is fetch_week_active_bookings'
    result; `walker_ids` are the grid's columns, in order.

        [{'date': date, 'label': 'Mon', 'rows': [
            {'slot': 'Morning', 'is_drop_in': False, 'label': 'Walks',
             'unassigned': 2,                       # requested + waitlisted
             'cells': [[Booking, ...], ...]},       # one list per walker_ids entry
        ]}, ...]

    Cells hold confirmed bookings only, in pickup order. A drop-in row is
    left out when it has no bookings at all (assigned or not); walk rows are
    always present. Day-care slots (Full Day / Half Day) aren't offered and
    don't appear."""
    days = []
    for i in range(WEEKDAYS):
        d = week_start + timedelta(days=i)
        rows = []
        for slot, drop_in, label in WEEK_GRID_ROWS:
            lane = [b for b in bookings
                    if b.date == d and b.slot == slot and is_drop_in(b) == drop_in]
            if drop_in and not lane:
                continue
            confirmed = sorted(
                (b for b in lane if b.status in Booking.WALKER_STATUSES and b.walker_id),
                key=lambda b: (b.pickup_order or 999, b.dog.name if b.dog else ''),
            )
            rows.append({
                'slot': slot,
                'is_drop_in': drop_in,
                'label': label,
                'unassigned': sum(1 for b in lane if b.status in Booking.PENDING_STATUSES),
                'cells': [[b for b in confirmed if b.walker_id == wid] for wid in walker_ids],
            })
        days.append({'date': d, 'label': WEEKDAY_LABELS[i], 'rows': rows})
    return days


def both_walks_keys(bookings):
    """{(dog_id, date)} for dogs with group walks in both the Morning and the
    Afternoon that day — the Week view's both-walks marker. Same rule as the
    board's both_slots_dog_ids: walks only, any active status."""
    slots = {}
    for b in bookings:
        if not is_drop_in(b):
            slots.setdefault((b.dog_id, b.date), set()).add(b.slot)
    return {key for key, s in slots.items() if {'Morning', 'Afternoon'} <= s}

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
    admin Week view's walker columns + format_walker_week_text."""
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
    the admin Week view's "Copy team schedule" text and the walker weekly
    view (walker/routes.py)."""
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
    on the admin Week view ("Copy team schedule"), e.g.:

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
    Afternoon, skipping slots with nothing booked. Building block for
    format_walker_week_text (the Week view's per-walker clipboard text)."""
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
