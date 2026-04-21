"""Capacity checking logic for booking availability."""

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.models import WalkerSchedule, WalkerUnavailability, WalkerAdHocAvailability, Booking, ServiceType, Walker
from app import db


def get_available_walkers(date, slot, drop_in=False, ignore_unavailability=False):
    """Return list of active walkers scheduled for a given date + slot.

    Includes walkers from their default weekly schedule plus any ad hoc
    availability entries for this specific date+slot.
    If drop_in=True, only returns walkers with does_drop_ins=True.
    By default excludes walkers with unavailability exceptions for this date/slot.
    Pass ignore_unavailability=True to include them (e.g. for admin override checks).
    """
    day_of_week = date.weekday()  # 0=Monday, 6=Sunday

    # Walkers scheduled by default for this day+slot
    schedules = (
        WalkerSchedule.query
        .filter_by(day_of_week=day_of_week, slot=slot, active=True)
        .all()
    )
    scheduled_walker_ids = {s.walker_id for s in schedules}

    # Walkers with ad hoc availability for this specific date+slot
    adhoc = (
        WalkerAdHocAvailability.query
        .filter_by(date=date, slot=slot)
        .all()
    )
    adhoc_walker_ids = {a.walker_id for a in adhoc}

    # Union of both sets
    all_walker_ids = scheduled_walker_ids | adhoc_walker_ids

    unavail_walker_ids = set()
    if not ignore_unavailability:
        unavail = (
            WalkerUnavailability.query
            .filter_by(date=date, slot=slot)
            .all()
        )
        unavail_walker_ids = {u.walker_id for u in unavail}

    # Eager-load user to avoid N+1 on w.user.active
    all_walkers = (
        Walker.query
        .filter(Walker.id.in_(all_walker_ids))
        .options(joinedload(Walker.user))
        .all()
    )
    walkers = [
        w for w in all_walkers
        if w.user.active and w.id not in unavail_walker_ids
    ]

    if drop_in:
        walkers = [w for w in walkers if w.does_drop_ins]

    return walkers


def get_max_per_walker(service_slug=ServiceType.WALK):
    """Get the max capacity per walker from ServiceType config.

    Result is cached in Flask's g for the duration of the request so repeated
    calls within a single booking operation don't hit the DB multiple times.
    """
    try:
        from flask import g
        if not hasattr(g, '_max_per_walker'):
            g._max_per_walker = {}
        if service_slug in g._max_per_walker:
            return g._max_per_walker[service_slug]
        cache = g._max_per_walker
    except RuntimeError:
        cache = None  # no request context (tests, CLI)

    service = ServiceType.query.filter_by(slug=service_slug).first()
    result = service.default_max_capacity if (service and service.default_max_capacity) else 6

    if cache is not None:
        cache[service_slug] = result
    return result


def get_walk_capacity(date, slot):
    """Return (total_slots, booked_slots, available_slots) for group walks on a date+slot."""
    walkers = get_available_walkers(date, slot, drop_in=False)
    max_per_walker = get_max_per_walker(ServiceType.WALK)
    total_slots = len(walkers) * max_per_walker

    booked = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date == date,
            Booking.slot == slot,
            Booking.status.in_(Booking.CAPACITY_STATUSES),
            ServiceType.slug == ServiceType.WALK,
        )
        .count()
    )

    return total_slots, booked, max(0, total_slots - booked)


def get_drop_in_capacity(date, slot):
    """Return (total_slots, booked_slots, available_slots) for drop-ins on a date+slot."""
    walkers = get_available_walkers(date, slot, drop_in=True)
    max_per_walker = get_max_per_walker(ServiceType.DROP_IN)
    total_slots = len(walkers) * max_per_walker

    booked = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date == date,
            Booking.slot == slot,
            Booking.status.in_(Booking.CAPACITY_STATUSES),
            ServiceType.slug == ServiceType.DROP_IN,
        )
        .count()
    )

    return total_slots, booked, max(0, total_slots - booked)


def get_walker_slot_count(walker_id, date, slot, service_slug=None):
    """Count active bookings assigned to a specific walker for a date/slot.

    Optionally filters by service type slug.
    """
    q = Booking.query.filter(
        Booking.walker_id == walker_id,
        Booking.date == date,
        Booking.slot == slot,
        Booking.status.in_(Booking.CAPACITY_STATUSES),
    )
    if service_slug:
        q = q.join(ServiceType).filter(ServiceType.slug == service_slug)
    return q.count()


def get_daycare_capacity(date):
    """Return (total_slots, booked_slots, available_slots) for daycare on a date."""
    service = ServiceType.query.filter_by(slug=ServiceType.DAY_CARE).first()
    if not service or not service.default_max_capacity:
        return 0, 0, 0

    total = service.default_max_capacity

    booked = (
        Booking.query
        .filter(
            Booking.date == date,
            Booking.service_type_id == service.id,
            Booking.status.in_(Booking.CAPACITY_STATUSES),
        )
        .count()
    )

    return total, booked, max(0, total - booked)


def auto_assign_walker(date, slot, service_slug=ServiceType.WALK):
    """Return the least-loaded available walker for a date+slot who still has capacity.

    Picks the walker with the fewest confirmed/requested bookings for that slot,
    as long as they're under max_per_walker. Returns None if no walker has space.
    """
    drop_in = (service_slug == ServiceType.DROP_IN)
    walkers = get_available_walkers(date, slot, drop_in=drop_in)
    if not walkers:
        return None

    max_cap = get_max_per_walker(service_slug)
    walker_ids = [w.id for w in walkers]

    # Single GROUP BY query instead of one COUNT per walker
    q = (
        db.session.query(Booking.walker_id, func.count(Booking.id).label('cnt'))
        .filter(
            Booking.walker_id.in_(walker_ids),
            Booking.date == date,
            Booking.slot == slot,
            Booking.status.in_(Booking.CAPACITY_STATUSES),
        )
    )
    if service_slug:
        q = q.join(ServiceType).filter(ServiceType.slug == service_slug)
    counts = {row.walker_id: row.cnt for row in q.group_by(Booking.walker_id).all()}

    best_walker = None
    best_count = max_cap  # only accept walkers strictly under capacity
    for walker in walkers:
        count = counts.get(walker.id, 0)
        if count < best_count:
            best_count = count
            best_walker = walker

    return best_walker


def check_availability(service_type, date, slot=None, admin_override=False):
    """Check if a booking can be made for the given service, date, and slot.
    Returns (available: bool, can_waitlist: bool, message: str).

    Pass admin_override=True to bypass the "no available walkers" block when all
    walkers are marked unavailable. Admin-created bookings are assigned manually
    on the board, so capacity is not a hard constraint. The slot must still have
    at least one walker scheduled (ignoring unavailability) to allow override.
    """
    if service_type.slug == ServiceType.WALK:
        if not slot:
            return False, False, "Slot is required for walk bookings."
        total, booked, available = get_walk_capacity(date, slot)
        if total == 0:
            if admin_override:
                # Allow if walkers are scheduled but all marked unavailable
                scheduled = get_available_walkers(date, slot, ignore_unavailability=True)
                if scheduled:
                    return True, False, "Admin override: walkers are marked unavailable but booking created."
            return False, False, f"No walkers are scheduled for {slot} on {date.strftime('%A %d %b')}."
        if available <= 0:
            return False, True, f"All {total} walk slots are booked for {slot} on {date.strftime('%d %b')}. You can join the waitlist."
        return True, False, f"{available} of {total} slots available."

    elif service_type.slug == ServiceType.DROP_IN:
        if not slot:
            return False, False, "Slot is required for drop-in bookings."
        total, booked, available = get_drop_in_capacity(date, slot)
        if total == 0:
            if admin_override:
                scheduled = get_available_walkers(date, slot, drop_in=True, ignore_unavailability=True)
                if scheduled:
                    return True, False, "Admin override: drop-in walkers are marked unavailable but booking created."
            return False, False, f"No drop-in visits are available for {slot} on {date.strftime('%A %d %b')}."
        if available <= 0:
            return False, True, f"All {total} drop-in slots are booked for {slot} on {date.strftime('%d %b')}. You can join the waitlist."
        return True, False, f"{available} of {total} slots available."

    elif service_type.slug == ServiceType.DAY_CARE:
        total, booked, available = get_daycare_capacity(date)
        if available <= 0:
            return False, True, f"Day care is fully booked for {date.strftime('%d %b')} ({total} dogs max). You can join the waitlist."
        return True, False, f"{available} of {total} spots available."

    return True, False, "Availability check not implemented for this service type."


def get_slot_availability_summary(date):
    """Return availability info for both slots on a given date for group walks.

    Returns: {
        'Morning': {'total': 12, 'booked': 8, 'available': 4},
        'Afternoon': {'total': 6, 'booked': 2, 'available': 4},
    }
    """
    result = {}
    for slot in ('Morning', 'Afternoon'):
        total, booked, available = get_walk_capacity(date, slot)
        result[slot] = {
            'total': total,
            'booked': booked,
            'available': available,
        }
    return result


def get_drop_in_availability_summary(date):
    """Return drop-in availability info for both slots on a given date.

    Returns: {
        'Morning': {'total': 6, 'booked': 2, 'available': 4},
        'Afternoon': {'total': 6, 'booked': 1, 'available': 5},
    }
    """
    result = {}
    for slot in ('Morning', 'Afternoon'):
        total, booked, available = get_drop_in_capacity(date, slot)
        result[slot] = {
            'total': total,
            'booked': booked,
            'available': available,
        }
    return result
