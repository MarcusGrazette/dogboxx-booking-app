"""Who hears about a booking action — the one place the recipient rules live.

Three audiences, each minus the actor (people are never told what they just
did; the toast already said so):

  * household — every *current* owner of the booking's dog (DogOwner rows),
    all getting the same text. Not booking.user_id, which is attribution
    only: a revoked co-owner who made a booking stops hearing about it.
  * admins    — every is_admin user, for client-initiated actions only;
    other admins' actions are in the activity feed.
  * walker    — the walker whose own confirmed list changed, only inside the
    urgent window (NotificationBatch.add_for_walker → walker_notify_now).

One notification per person per action: when someone fits more than one
audience, household beats admin beats walker. The full matrix (incl. the
board/cancel/availability actions still to move here) is in the handbook,
deep-dives/notifications.md "Notification redesign".

Like NotificationBatch, nothing here commits — the caller does.
"""

from app.models import DogOwner, ServiceType, User
from app.utils.notifications import NotificationBatch, create_notification


def _svc_label(booking):
    st = booking.service_type
    return 'drop-in' if st and st.slug == ServiceType.DROP_IN else 'walk'


def _kind(booking):
    if booking.status == 'confirmed':
        return 'booking_confirmed'
    if booking.status == 'waitlisted':
        return 'booking_waitlisted'
    return 'booking_requested'


def _household_ids(dog_ids):
    """{dog_id: [user_id, ...]} of current owners, in one query."""
    owners = {}
    for o in DogOwner.query.filter(DogOwner.dog_id.in_(dog_ids)).order_by(DogOwner.id):
        owners.setdefault(o.dog_id, []).append(o.user_id)
    return owners


def _admin_ids():
    return [u.id for u in User.query.filter_by(is_admin=True).order_by(User.id)]


def _fmt_day(d):
    return d.strftime('%a %-d %b')


def _same_day_admin_notice(bookings, actor):
    """(title, link) of the urgent admin notice for same-day pending bookings.
    summarise() has no same-day kind, so this stays a direct create_notification."""
    b0 = bookings[0]
    svc = _svc_label(b0)
    dog_names = sorted({b.dog.name for b in bookings if b.dog})
    slots = ' & '.join(s.lower() for s in sorted({b.slot for b in bookings},
                                                 key=lambda s: s != 'Morning'))
    plural = 's' if len(bookings) > 1 else ''
    title = (f"Same-day request — {actor.firstname} requested "
             f"{', '.join(dog_names)}'s {slots} {svc}{plural} on {_fmt_day(b0.date)}")
    link = '/admin/drop-in-board' if svc == 'drop-in' else '/admin'
    return title, link


def notify_created(bookings, *, actor, by_admin, same_day=False):
    """Notify about bookings just created by one action (a single booking, both
    slots, or a recurring series). Bookings must be flushed or committed.

    by_admin: an admin booking on a client's behalf (/admin routes) — the
    household hears, other admins don't. Otherwise a client booking: the
    household (minus the client) and every admin hear.
    same_day: a client's same-day booking — pending ones reach admins as one
    urgent same_day_request notice instead of the grouped kinds.
    """
    bookings = [b for b in bookings if b is not None]
    if not bookings:
        return

    owners = _household_ids({b.dog_id for b in bookings})
    household = []
    for b in bookings:
        for uid in owners.get(b.dog_id, []):
            if uid != actor.id and uid not in household:
                household.append(uid)
    admins = [] if by_admin else [
        uid for uid in _admin_ids() if uid != actor.id and uid not in household]
    told = set(household) | set(admins)

    batch = NotificationBatch(actor_id=actor.id)
    urgent = []
    for b in bookings:
        dog_name = b.dog.name if b.dog else 'Unknown dog'
        svc = _svc_label(b)
        kind = _kind(b)
        payload = dict(dog_name=dog_name, slot=b.slot, date=b.date, svc_label=svc)
        walker_user_id = b.walker.user_id if kind == 'booking_confirmed' and b.walker else None

        for uid in owners.get(b.dog_id, []):
            if uid in household:
                # No walker_name: reassignments are silent to the household,
                # so "Booked with Alice" could go out of date.
                batch.add(uid, kind, actor_first=actor.firstname, link='/', **payload)

        if same_day and kind != 'booking_confirmed':
            urgent.append(b)
        else:
            walker_first = (b.walker.user.firstname
                            if walker_user_id and b.walker.user else None)
            link = (f'/admin/clients/{actor.id}' if kind == 'booking_confirmed'
                    else '/admin/drop-in-board' if svc == 'drop-in' else '/admin')
            for uid in admins:
                batch.add(uid, kind, actor_first=actor.firstname, link=link,
                          walker_name=walker_first, **payload)

        if walker_user_id and walker_user_id != actor.id and walker_user_id not in told:
            batch.add_for_walker(walker_user_id, 'walker_assigned', **payload)

    if urgent and admins:
        title, link = _same_day_admin_notice(urgent, actor)
        for uid in admins:
            create_notification(recipient_id=uid, notification_type='same_day_request',
                                title=title, link=link, sender_id=actor.id)
    batch.flush()
