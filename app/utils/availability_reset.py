"""Shared reset for confirmed bookings that lose their walker's availability.

Every path that removes or shrinks a walker's availability for a (date, slot)
must reset any confirmed booking still sitting on it — otherwise it stays
'confirmed' in the DB while rendering nowhere on /admin/board (the board
derives its walker columns from availability). See CLAUDE.md "Walker
availability change must reset confirmed bookings" for the full list of
call sites.
"""

import uuid

from app.models import Booking
from app.utils.booking_status import bulk_transition
from app.utils.dates import local_today
from app.utils.notifications import NotificationBatch


def reset_bookings_for_lost_availability(bookings, *, actor_id, transition_batch_id=None,
                                          reason=None, notify_walker=True):
    """Reset `bookings` to requested/unassigned.

    `reason` is a short human-readable clause (no trailing punctuation, e.g.
    "Alice marked unavailable") stamped onto each reset BookingStatusChange
    row's `notes` — the activity feed uses its presence to file these rows
    under "Availability changes" and to say why the booking was reset,
    instead of a bare "Reset ... to requested". Every call site should pass
    one; omit only for a reset with no availability-change cause to report.

    Notifications (notification redesign): the household is not told — a
    reset walk is reassigned by an admin, and the household only hears again
    if it ends up cancelled. The walker who had each walk is told it's off
    their list (grouped, inside the urgent window) unless they made the
    change themselves; `notify_walker=False` for a walker leaving the pool
    (deactivated, walker role removed). Admins are told by the caller, and
    only for a walker's own change (booking_notify.notify_walker_dropout).
    Does not commit.
    """
    if not bookings:
        return
    batch = NotificationBatch(actor_id=actor_id)
    if notify_walker:
        # Read before bulk_transition clears walker_id.
        for b in bookings:
            walker_uid = b.walker.user_id if b.walker_id and b.walker else None
            if walker_uid and walker_uid != actor_id:
                batch.add_for_walker(
                    walker_uid, 'walker_removed',
                    dog_name=b.dog.name if b.dog else 'Unknown dog', slot=b.slot, date=b.date,
                    svc_label='drop-in' if b.service_type and b.service_type.slug == 'drop-in' else 'walk')
    bulk_transition(bookings, 'requested', actor_id=actor_id, walker_id=None,
                    batch_id=transition_batch_id or uuid.uuid4().hex, notes=reason)
    batch.flush()


def reset_future_bookings_for_walker(walker, *, actor_id, reason):
    """Reset every future confirmed booking assigned to `walker`, for paths
    where the walker leaves the pool entirely (deactivation from either the
    Walkers or Clients page, walker-role removal). Nobody is notified: the
    household never hears about resets and the walker is leaving. Does not
    commit. Returns the reset bookings.
    """
    affected = Booking.query.filter(
        Booking.walker_id == walker.id,
        Booking.date >= local_today(),
        Booking.status == 'confirmed',
    ).all()
    reset_bookings_for_lost_availability(affected, actor_id=actor_id, reason=reason,
                                         notify_walker=False)
    return affected
