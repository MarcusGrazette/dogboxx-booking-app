"""Recipient resolver for admin broadcasts.

A broadcast scope is a (date, scope_slot) pair where scope_slot is one of:
    'all'        — match every booking on that date
    'morning'    — Morning, Half Day AM, Full Day
    'afternoon'  — Afternoon, Half Day PM, Full Day

Recipients are unique Users (primary + secondary co-owners) who have at least
one going-ahead booking matching the scope. The resolver returns each user
once with the list of their affected dogs, so the preview UI can show
"Marcus — Daisy, Pip" rather than two separate rows.
"""

from app import db
from app.models import Booking, Broadcast, Dog, DogOwner, User


# Bookings that are "going to happen" — confirmed, or confirmed-then-slot-adjusted.
# Mirrors the (confirmed, modified) pairing used elsewhere in the codebase.
BROADCAST_STATUSES = ('confirmed', 'modified')


def _slots_for_scope(scope_slot):
    """Return the booking-slot values that match a scope_slot."""
    if scope_slot == Broadcast.SCOPE_MORNING:
        return ('Morning', 'Half Day AM', 'Full Day')
    if scope_slot == Broadcast.SCOPE_AFTERNOON:
        return ('Afternoon', 'Half Day PM', 'Full Day')
    # SCOPE_ALL — every slot value
    return ('Morning', 'Afternoon', 'Full Day', 'Half Day AM', 'Half Day PM')


def resolve_recipients(scope_date, scope_slot):
    """Return [(User, [Dog, ...]), ...] for the given scope, sorted by firstname.

    Each user appears at most once. Dogs are listed in the order discovered,
    deduplicated per user.
    """
    if scope_slot not in Broadcast.VALID_SCOPES:
        raise ValueError(f"invalid scope_slot: {scope_slot}")

    slot_values = _slots_for_scope(scope_slot)

    bookings = (
        db.session.query(Booking.dog_id)
        .filter(Booking.date == scope_date)
        .filter(Booking.status.in_(BROADCAST_STATUSES))
        .filter(Booking.slot.in_(slot_values))
        .distinct()
        .all()
    )
    dog_ids = [b.dog_id for b in bookings]
    if not dog_ids:
        return []

    # Batch-fetch owners + dogs to avoid N+1.
    ownerships = (
        DogOwner.query
        .filter(DogOwner.dog_id.in_(dog_ids))
        .all()
    )
    user_ids = {o.user_id for o in ownerships}
    users_by_id = {
        u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()
    }
    dogs_by_id = {
        d.id: d for d in Dog.query.filter(Dog.id.in_(dog_ids)).all()
    }

    # user_id → (User, [Dog, ...])
    grouped = {}
    for o in ownerships:
        user = users_by_id.get(o.user_id)
        dog = dogs_by_id.get(o.dog_id)
        if not user or not dog or not user.active:
            continue
        if user.id not in grouped:
            grouped[user.id] = (user, [])
        if dog not in grouped[user.id][1]:
            grouped[user.id][1].append(dog)

    return sorted(
        grouped.values(),
        key=lambda pair: ((pair[0].firstname or '').lower(), pair[0].id),
    )


def scope_slot_label(scope_slot):
    """Human-readable label for a scope_slot value."""
    return {
        Broadcast.SCOPE_ALL: 'All day',
        Broadcast.SCOPE_MORNING: 'Morning',
        Broadcast.SCOPE_AFTERNOON: 'Afternoon',
    }.get(scope_slot, scope_slot)
