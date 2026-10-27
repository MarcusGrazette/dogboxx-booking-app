"""Client & dog deletion — anonymise in place (FEATURES #100).

A super-admin deletes a dog (optionally with any of its owners), or a client
who owns no dogs. Personal data is wiped, but the User/Client/Dog rows, their
ids and the DogOwner links all stay, so past walks, invoices and the revenue
rollup are unchanged:

  * billing is recomputed from bookings every time, grouped per dog
    (double-slot discount keys by (dog_id, date)) and per household (weekly
    discount) — repointing bookings to a shared "Deleted dog" would invent
    discounts in past months, and two dogs walked in the same slot couldn't
    both move onto one placeholder (ix_booking_dog_date_slot_active);
  * invoices and revenue find a dog through its primary owner, so the
    DogOwner rows must stay even for owners who weren't deleted.

The routes (admin/views/deletion.py) call check_* first and repeat every check
on POST; the anonymise_* helpers mutate and queue, never commit. Photo files
are returned to the caller, which deletes them only after its commit
succeeds (same rule as uploads.delete_replaced_photo).
"""

import secrets
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    ActivityLog, Booking, BookingStatusChange, Dog, DogOwner, Notification, User,
)
from app.utils.dates import local_today
from app.utils.uploads import delete_replaced_photo

# Bookings that still count as "upcoming" — everything except the terminal
# statuses (incl. the fossil 'completed'), so 'modified' blocks too. Matches
# the ix_booking_dog_date_slot_active predicate.
_FINISHED_STATUSES = ('cancelled', 'rejected', 'completed')


def deleted_dog_label(dog_id):
    return f"[Deleted dog #{dog_id}]"


def deleted_client_label(user_id):
    return f"[Deleted client #{user_id}]"


def future_bookings_for_dog(dog_id):
    """Bookings from today (London) on that would block the delete."""
    return (
        Booking.query
        .filter(Booking.dog_id == dog_id,
                Booking.date >= local_today(),
                Booking.status.notin_(_FINISHED_STATUSES))
        .order_by(Booking.date, Booking.id)
        .all()
    )


def _other_live_dog_names(user_id, exclude_dog_id=None):
    q = (
        Dog.query.join(DogOwner)
        .filter(DogOwner.user_id == user_id, Dog.deleted_at.is_(None))
    )
    if exclude_dog_id is not None:
        q = q.filter(Dog.id != exclude_dog_id)
    return [d.name for d in q.order_by(Dog.name).all()]


def client_block_reason(user, actor, *, exclude_dog_id=None):
    """Why `user` can't be deleted by `actor`, or None if they can.

    `exclude_dog_id` is the dog being deleted alongside them — owning only
    that dog (plus already-deleted dogs) is fine.
    """
    if user.deleted_at:
        return "has already been deleted"
    if user.id == actor.id:
        return "is you"
    if user.is_super_admin:
        return "is the business owner"
    if user.role == 'walker' or user.is_admin:
        return "is a walker (remove walker status first)"
    others = _other_live_dog_names(user.id, exclude_dog_id)
    if others:
        return f"also owns {' & '.join(others)}"
    return None


def dog_owners(dog_id):
    """The dog's owners, primary first, as (DogOwner, User) pairs."""
    rows = (
        DogOwner.query.filter_by(dog_id=dog_id)
        .order_by(DogOwner.role, DogOwner.id)  # 'primary' < 'secondary'
        .all()
    )
    return [(o, o.user) for o in rows if o.user]


def _photo(filename, subfolder):
    return [(filename, subfolder)] if filename else []


def anonymise_dog(dog):
    """Wipe a dog's personal data and free text. Returns the photo files to
    delete once the caller has committed."""
    label = deleted_dog_label(dog.id)
    photos = _photo(dog.pic, 'dogs') + _photo(dog.pickup_notes_photo, 'pickup_notes')

    dog.name = label
    dog.breed = None
    dog.date_of_birth = None
    dog.gender = None
    dog.allergies = None
    dog.other_info = None
    dog.whatsapp_group_url = None
    dog.pickup_instructions = None
    dog.hold_key = False
    dog.pic = None
    dog.pickup_notes_photo = None
    dog.deleted_at = datetime.now(timezone.utc)

    booking_ids = db.session.query(Booking.id).filter(Booking.dog_id == dog.id)
    Booking.query.filter(Booking.dog_id == dog.id).update(
        {Booking.client_notes: None, Booking.admin_notes: None},
        synchronize_session=False,
    )
    # Notes only — old_slot/new_slot/walker_id stay, the feed reads them.
    BookingStatusChange.query.filter(
        BookingStatusChange.booking_id.in_(booking_ids.scalar_subquery())
    ).update({BookingStatusChange.notes: None}, synchronize_session=False)

    _rewrite_activity('dog', [dog.id], label)
    _rewrite_activity('booking_note', booking_ids.scalar_subquery(), label)
    return photos


def anonymise_client(user):
    """Wipe a client's personal data and end every way back into the
    account. Returns the photo files to delete once the caller has committed."""
    label = deleted_client_label(user.id)
    photos = _photo(user.profile_pic, 'profiles')

    # full_name joins the two halves with a space → "[Deleted client #123]".
    user.firstname = "[Deleted"
    user.lastname = f"client #{user.id}]"
    # Unique, and .invalid can never receive mail; frees the real address so
    # the person can be added again later.
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.phone = None
    user.profile_pic = None
    user.email_marketing = False
    user.active = False
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None
    user.hashed_password = generate_password_hash(secrets.token_urlsafe(32))
    # Ends every session (user_loader also refuses inactive users) and drops
    # all push subscriptions.
    user.rotate_session_token()
    user.deleted_at = datetime.now(timezone.utc)

    client = user.client
    if client:
        client.street_address = None
        client.city = None
        client.state = None
        client.postal_code = None
        client.country = None
        client.maps_url = None

    # Their own bell. Notifications they sent to others stay (decision 6).
    Notification.query.filter_by(recipient_id=user.id).delete(synchronize_session=False)
    _rewrite_activity('client', [user.id], label)
    return photos


def _rewrite_activity(entity_type, entity_ids, label):
    """Replace the summary (which names the entity) and drop the changes."""
    ActivityLog.query.filter(
        ActivityLog.entity_type == entity_type,
        ActivityLog.entity_id.in_(entity_ids),
    ).update(
        {ActivityLog.summary: f"Change recorded for {label}", ActivityLog.changes: None},
        synchronize_session=False,
    )


def delete_photos(photos):
    """Call after the commit. Never raises (delete_replaced_photo doesn't)."""
    for filename, subfolder in photos:
        delete_replaced_photo(filename, subfolder)


def live_client_query():
    """Client users who haven't been deleted (for lists and pickers)."""
    return User.query.filter(User.client != None, User.deleted_at.is_(None))  # noqa: E711
