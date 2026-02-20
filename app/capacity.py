"""Capacity checking logic for booking availability."""

from app.models import WalkerSchedule, Booking, ServiceType
from app import db


def get_available_walkers(date, slot):
    """Return list of active walkers scheduled for a given date + slot."""
    day_of_week = date.weekday()  # 0=Monday, 6=Sunday
    schedules = (
        WalkerSchedule.query
        .filter_by(day_of_week=day_of_week, slot=slot, active=True)
        .all()
    )
    return [s.walker for s in schedules if s.walker.user.active]


def get_walk_capacity(date, slot):
    """Return (total_slots, booked_slots, available_slots) for walks on a date+slot."""
    walkers = get_available_walkers(date, slot)
    max_per_walker = 6  # Could be made configurable via ServiceType.default_max_capacity
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
    Returns (available: bool, message: str)."""
    if service_type.slug == 'group-walk':
        if not slot:
            return False, "Slot is required for walk bookings."
        total, booked, available = get_walk_capacity(date, slot)
        if available <= 0:
            return False, f"No walk slots available for {date} {slot} (all {total} slots booked)."
        return True, f"{available} of {total} slots available."

    elif service_type.slug == 'day-care':
        total, booked, available = get_daycare_capacity(date)
        if available <= 0:
            return False, f"Day care is fully booked for {date} ({total} dogs max)."
        return True, f"{available} of {total} spots available."

    return True, "Availability check not implemented for this service type."
