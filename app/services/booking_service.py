"""Booking creation service — canonical path for all new booking creation.

Every booking creation site must use create_booking() so that:
  1. The advisory lock is acquired before the capacity check (no TOCTOU race).
  2. record_booking_created() is always called, writing the audit row.
  3. The shared batch_id ties the creation BSC row to any immediate auto-confirm
     BSC row, which the activity feed uses to suppress the spurious "Requested"
     entry when a booking is immediately confirmed (CLAUDE.md §batch_id rule).

Callers remain responsible for notifications — notification wording and grouping
differ enough between client/admin and single/bulk paths that embedding them here
would require complex parameterisation with no real benefit.
"""

from app import db
from app.models import Booking, ServiceType
from app.capacity import (
    acquire_booking_lock,
    check_availability,
    auto_assign_walker,
    get_walker_slot_count,
)
from app.utils.booking_status import record_booking_created, transition_booking


class CapacityError(Exception):
    """Raised when a slot has no walkers and no waitlist (hard reject)."""


def create_booking(
    *,
    dog,
    user_id,
    date,
    slot,
    service,
    actor_id,
    batch_id,
    auto_confirm=True,
    admin_override=False,
    same_day=False,
    created_by_id=None,
):
    """Lock → capacity-check → create → flush → audit → optional auto-assign.

    Returns ``(booking, auto_confirmed: bool)``. Never commits; caller must commit.

    Raises CapacityError if the slot has no walkers and no waitlist.

    Parameters
    ----------
    dog             Dog model instance (needed for dog_id).
    user_id         Owner of the booking (the client the booking is *for*).
    date            datetime.date the booking is on.
    slot            'Morning' or 'Afternoon'.
    service         ServiceType instance for this booking.
    actor_id        User.id of the person performing the action (audit / BSC row).
    batch_id        Shared hex string for this request; ties the creation BSC row to
                    any immediate auto-confirm BSC row so the activity feed can group.
    auto_confirm    If True and capacity is available and not same_day and not drop-in,
                    attempt auto-assign and transition to 'confirmed'.
    admin_override  Passed through to check_availability; lets admins book past/closed dates.
    same_day        Forces initial status to 'requested' (no waitlist, no auto-assign).
    created_by_id   Set to the admin's user_id when booking on behalf of a client.
    """
    acquire_booking_lock(service.slug, date, slot)
    available, can_waitlist, capacity_msg = check_availability(
        service, date, slot, admin_override=admin_override
    )
    # same_day bookings bypass the no-walkers hard reject — owner assigns manually.
    if not available and not can_waitlist and not same_day:
        raise CapacityError(capacity_msg)

    # same_day bookings always land as 'requested' (no waitlist, manual admin review).
    # Otherwise: waitlisted when full, requested when capacity is available.
    initial_status = 'requested' if (same_day or available) else 'waitlisted'

    booking = Booking(
        user_id=user_id,
        dog_id=dog.id,
        service_type_id=service.id,
        date=date,
        slot=slot,
        status=initial_status,
        created_by_id=created_by_id,
    )
    db.session.add(booking)
    db.session.flush()  # populate booking.id before the audit row
    record_booking_created(booking, actor_id=actor_id, batch_id=batch_id)

    auto_confirmed = False
    is_drop_in = (service.slug == ServiceType.DROP_IN)
    if auto_confirm and available and not same_day and not is_drop_in:
        walker = auto_assign_walker(date, slot, service_slug=service.slug)
        if walker:
            transition_booking(
                booking, 'confirmed',
                actor_id=actor_id,
                walker_id=walker.id,
                batch_id=batch_id,
            )
            booking.pickup_order = get_walker_slot_count(
                walker.id, date, slot, service_slug=service.slug
            )
            auto_confirmed = True

    return booking, auto_confirmed
