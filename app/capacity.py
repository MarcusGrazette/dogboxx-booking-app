"""Capacity checking logic for booking availability."""

from app.models import WalkerSchedule, WalkerUnavailability, Booking, ServiceType, Walker
from app import db


def get_available_walkers(date, slot, drop_in=False):
    """Return list of active walkers scheduled for a given date + slot.

    If drop_in=True, only returns walkers with does_drop_ins=True.
    Excludes walkers with unavailability exceptions for this date/slot.
    """
    day_of_week = date.weekday()  # 0=Monday, 6=Sunday

    schedules = (
        WalkerSchedule.query
        .filter_by(day_of_week=day_of_week, slot=slot, active=True)
        .all()
    )

    unavail = (
        WalkerUnavailability.query
        .filter_by(date=date, slot=slot)
        .all()
    )
    unavail_walker_ids = {u.walker_id for u in unavail}

    walkers = [
        s.walker for s in schedules
        if s.walker.user.active and s.walker_id not in unavail_walker_ids
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


def check_availability(service_type, date, slot=None):
    """Check if a booking can be made for the given service, date, and slot.
    Returns (available: bool, can_waitlist: bool, message: str)."""
    if service_type.slug == 'group-walk':
        if not slot:
            return False, False, "Slot is required for walk bookings."
        total, booked, available = get_walk_capacity(date, slot)
        if total == 0:
            return False, False, f"No walkers are scheduled for {slot} on {date.strftime('%A %d %b')}."
        if available <= 0:
            return False, True, f"All {total} walk slots are booked for {slot} on {date.strftime('%d %b')}. You can join the waitlist."
        return True, False, f"{available} of {total} slots available."

    elif service_type.slug == 'drop-in':
        if not slot:
            return False, False, "Slot is required for drop-in bookings."
        total, booked, available = get_drop_in_capacity(date, slot)
        if total == 0:
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
