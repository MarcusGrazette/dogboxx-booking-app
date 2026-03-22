"""
Booking access helpers for multi-owner support.

These utilities centralise the logic for deciding which dogs and bookings
a user is allowed to see or act on.  A user has access to a dog if they
appear in the dog_owners table for that dog (regardless of role).

Usage
-----
    from app.utils.booking_access import get_accessible_dog_ids, user_can_access_booking

    # Query filter
    dog_ids = get_accessible_dog_ids(current_user.id)
    bookings = Booking.query.filter(Booking.dog_id.in_(dog_ids)).all()

    # Auth guard
    if not user_can_access_booking(current_user, booking):
        return jsonify(success=False, message="Unauthorized"), 403
"""

from app.models import DogOwner, Booking


def get_accessible_dog_ids(user_id: int) -> list[int]:
    """Return a list of dog IDs the user has any ownership record for.

    This includes both 'primary' and 'secondary' roles.  Returns an empty
    list if the user owns no dogs (e.g. a walker or admin calling this).
    """
    ownerships = DogOwner.query.filter_by(user_id=user_id).all()
    return [o.dog_id for o in ownerships]


def user_can_access_booking(user, booking: Booking) -> bool:
    """Return True if the user is allowed to view or act on a booking.

    A user can access a booking if:
    - They created the booking (booking.user_id == user.id), OR
    - They are an owner (primary or secondary) of the booked dog, OR
    - They are an admin.
    """
    if user.is_admin:
        return True
    if booking.user_id == user.id:
        return True
    return DogOwner.query.filter_by(dog_id=booking.dog_id, user_id=user.id).first() is not None
