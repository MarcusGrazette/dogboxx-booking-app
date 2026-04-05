"""Capacity checking logic for booking availability."""

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

    all_walkers = Walker.query.filter(Walker.id.in_(all_walker_ids)).all()
    walkers = [
        w for w in all_walkers
        if w.user.active and w.id not in unavail_walker_ids
    ]

    if drop_in:
        walkers = [w for w in walkers if w.does_drop_ins]

    return walkers


def get_max_per_walker(service_slug='group-walk'):
    """Get the max capacity per walker from ServiceType config."""
    service = ServiceType.query.filter_by(slug=service_slug).first()
    if service and service.default_max_capacity:
        return service.default_max_capacity
    return 6  # fallback default


def get_walk_capacity(date, slot):
    """Return (total_slots, booked_slots, available_slots) for group walks on a date+slot."""
    walkers = get_available_walkers(date, slot, drop_in=False)
    max_per_walker = get_max_per_walker('group-walk')
    total_slots = len(walkers) * max_per_walker

    booked = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date == date,
            Booking.slot == slot,
            Booking.status.in_(['requested', 'confirmed', 'modified']),
            ServiceType.slug == 'group-walk',
        )
        .count()
    )

    return total_slots, booked, max(0, total_slots - booked)


def get_drop_in_capacity(date, slot):
    """Return (total_slots, booked_slots, available_slots) for drop-ins on a date+slot."""
    walkers = get_available_walkers(date, slot, drop_in=True)
    max_per_walker = get_max_per_walker('drop-in')
    total_slots = len(walkers) * max_per_walker

    booked = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date == date,
            Booking.slot == slot,
            Booking.status.in_(['requested', 'confirmed', 'modified']),
            ServiceType.slug == 'drop-in',
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
        Booking.status.in_(['requested', 'confirmed', 'modified']),
    )
    if service_slug:
        q = q.join(ServiceType).filter(ServiceType.slug == service_slug)
    return q.count()


def get_daycare_capacity(date):
    """Return (total_slots, booked_slots, available_slots) for daycare on a date."""
    service = ServiceType.query.filter_by(slug='day-care').first()
    if not service or not service.default_max_capacity:
        return 0, 0, 0

    total = service.default_max_capacity

    booked = (
        Booking.query
        .filter(
            Booking.date == date,
            Booking.service_type_id == service.id,
            Booking.status.in_(['requested', 'confirmed', 'modified']),
        )
        .count()
    )

    return total, booked, max(0, total - booked)


def auto_assign_walker(date, slot, service_slug='group-walk'):
    """Return the least-loaded available walker for a date+slot who still has capacity.

    Picks the walker with the fewest confirmed/requested bookings for that slot,
    as long as they're under max_per_walker. Returns None if no walker has space.
    """
    drop_in = (service_slug == 'drop-in')
    walkers = get_available_walkers(date, slot, drop_in=drop_in)
    if not walkers:
        return None

    max_cap = get_max_per_walker(service_slug)
    best_walker = None
    best_count = max_cap  # only accept walkers strictly under capacity

    for walker in walkers:
        count = get_walker_slot_count(walker.id, date, slot, service_slug=service_slug)
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
    if service_type.slug == 'group-walk':
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

    elif service_type.slug == 'drop-in':
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

    elif service_type.slug == 'day-care':
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
