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
audience, household beats admin beats walker. Covers new bookings, the board
(assign / reassign / unassign / slot move / decline), cancellations and
closures; walker availability resets still use availability_reset.py. The
full matrix is in the handbook, deep-dives/notifications.md "Notification
redesign".

Like NotificationBatch, nothing here commits — the caller does.
"""

from app import db
from app.models import DogOwner, ServiceType, User, Walker
from app.utils.notifications import NotificationBatch, create_notification, walker_notify_now


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


def _slot_word(slot):
    return 'the afternoon' if slot == 'Afternoon' else 'the morning'


def _what(booking, slot=None):
    """"Rex's morning walk on Tue 21 Oct"."""
    dog_name = booking.dog.name if booking.dog else 'Unknown dog'
    return (f"{dog_name}'s {(slot or booking.slot).lower()} {_svc_label(booking)} "
            f"on {_fmt_day(booking.date)}")


class _Once:
    """One notification per person for a single-booking action: household,
    then admins, then walkers, each minus the actor and anyone already told."""

    def __init__(self, actor):
        self.actor = actor
        self.told = {actor.id}

    def send(self, uid, ntype, title, body=None, link='/'):
        if uid is None or uid in self.told:
            return
        self.told.add(uid)
        create_notification(recipient_id=uid, notification_type=ntype, title=title,
                            body=body, link=link, sender_id=self.actor.id)


def _walker_added(once, uid, booking):
    if walker_notify_now(booking.date):
        once.send(uid, 'walker_assigned',
                  f"You have been assigned a {_svc_label(booking)} on "
                  f"{booking.date.strftime('%-d %b %Y')}",
                  f"{booking.dog.name if booking.dog else 'Unknown dog'} — {booking.slot}",
                  link=f'/walker/pickups?date={booking.date.isoformat()}')


def _walker_removed(once, uid, booking, slot):
    if walker_notify_now(booking.date):
        once.send(uid, 'walker_removed', f"{_what(booking, slot)} is off your list",
                  link=f'/walker/pickups?date={booking.date.isoformat()}')


def notify_assigned(booking, *, actor, was_confirmed, old_walker_user_id, old_slot):
    """An admin gave `booking` a walker on the board (assign_walker): a first
    confirmation, a reassignment, a re-confirmation after a reset, and/or a
    slot move. Pass the pre-change state: `was_confirmed` = booking.confirmed_at
    was already set (it is never cleared, so a reset walk still counts),
    `old_walker_user_id`, `old_slot`.

    Household: told on the first confirmation and on a slot move — never on a
    reassignment or a re-confirmation. Walkers: whoever's list changed.
    """
    once = _Once(actor)
    moved = old_slot != booking.slot
    owners = _household_ids([booking.dog_id]).get(booking.dog_id, [])
    first = not was_confirmed
    if first or moved:
        when = f"{_fmt_day(booking.date)}"
        dog_name = booking.dog.name if booking.dog else 'Unknown dog'
        svc = _svc_label(booking)
        if first and moved:
            title = f"{dog_name}'s {svc} on {when} is confirmed for {_slot_word(booking.slot)}"
            ntype = 'booking_confirmed'
        elif first:
            title = f"{_what(booking)} confirmed"
            ntype = 'booking_confirmed'
        else:
            title = f"{dog_name}'s {svc} on {when} has moved to {_slot_word(booking.slot)}"
            ntype = 'system'
        body = f"Originally booked for {old_slot.lower()}." if moved else None
        for uid in owners:
            once.send(uid, ntype, title, body)

    # By id, not booking.walker: the caller read booking.walker for the old
    # walker, and setting walker_id doesn't refresh that loaded relationship.
    new_walker = db.session.get(Walker, booking.walker_id) if booking.walker_id else None
    new_walker_uid = new_walker.user_id if new_walker else None
    if old_walker_user_id and old_walker_user_id == new_walker_uid:
        if moved and walker_notify_now(booking.date):
            once.send(new_walker_uid, 'system',
                      f"{_what(booking, old_slot)} has moved to {_slot_word(booking.slot)}",
                      link=f'/walker/pickups?date={booking.date.isoformat()}')
        return
    if old_walker_user_id:
        _walker_removed(once, old_walker_user_id, booking, old_slot)
    _walker_added(once, new_walker_uid, booking)


def notify_unassigned(booking, *, actor, old_walker_user_id):
    """An admin took the walker off a confirmed walk (back to requested).
    Silent to the household; the walker who had it is told inside the window."""
    if old_walker_user_id:
        _walker_removed(_Once(actor), old_walker_user_id, booking, booking.slot)


def notify_declined(booking, *, actor):
    """An admin declined a request: every owner is told."""
    once = _Once(actor)
    for uid in _household_ids([booking.dog_id]).get(booking.dog_id, []):
        once.send(uid, 'booking_cancelled', f"{_what(booking)} has been declined",
                  "Please get in touch if you'd like to discuss.")


def notify_cancelled(bookings, *, actor, by_admin, reason=None):
    """Bookings cancelled by one action — a single cancel, Pause Walks, an
    admin bulk cancel, a closure. Call it BEFORE the transition: it reads each
    booking's walker, which the cancel clears.

    by_admin: the household (all owners) is told without an actor name, with
    `reason` as the body (a closure's reason; the household's defaults to
    "please get in touch"); other admins aren't told. Otherwise a client/co-owner cancel: the
    rest of the household and the admins are told "<Name> cancelled …".
    The walker who had each walk is told inside the urgent window.
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
    told = set(household) | set(admins) | {actor.id}
    actor_first = None if by_admin else actor.firstname
    household_reason = reason
    if by_admin and reason is None:
        household_reason = "Please get in touch if you'd like to discuss."

    batch = NotificationBatch(actor_id=actor.id)
    for b in bookings:
        payload = dict(dog_name=b.dog.name if b.dog else 'Unknown dog', slot=b.slot,
                       date=b.date, svc_label=_svc_label(b), reason=reason)
        for uid in owners.get(b.dog_id, []):
            if uid in household:
                batch.add(uid, 'booking_cancelled', actor_first=actor_first, link='/',
                          **{**payload, 'reason': household_reason})
        for uid in admins:
            batch.add(uid, 'booking_cancelled', actor_first=actor_first,
                      link=f'/admin/clients/{actor.id}', **payload)
        walker_uid = b.walker.user_id if b.walker_id and b.walker else None
        if walker_uid and walker_uid not in told:
            batch.add_for_walker(walker_uid, 'booking_cancelled', actor_first=actor_first,
                                 link='/walker/schedule', **payload)
    batch.flush()
