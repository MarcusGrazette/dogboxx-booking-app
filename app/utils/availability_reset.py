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


def reset_bookings_for_lost_availability(bookings, *, actor_id, transition_batch_id=None,
                                          reason=None):
    """Reset `bookings` to requested/unassigned.

    `reason` is a short human-readable clause (no trailing punctuation, e.g.
    "Alice marked unavailable") stamped onto each reset BookingStatusChange
    row's `notes` — the activity feed uses its presence to file these rows
    under "Availability changes" and to say why the booking was reset,
    instead of a bare "Reset ... to requested". Every call site should pass
    one; omit only for a reset with no availability-change cause to report.

    Nobody is notified here (notification redesign): the household only
    hears again if the walk is cancelled, and walkers only get the daily
    summary. Admins are told by the caller, and only for a walker's own
    change (booking_notify.notify_walker_dropout). Does not commit.
    """
    if not bookings:
        return
    bulk_transition(bookings, 'requested', actor_id=actor_id, walker_id=None,
                    batch_id=transition_batch_id or uuid.uuid4().hex, notes=reason)


def reset_future_bookings_for_walker(walker, *, actor_id, reason):
    """Reset every future confirmed booking assigned to `walker`, for paths
    where the walker leaves the pool entirely (deactivation from either the
    Walkers or Clients page, walker-role removal). Does not commit. Returns
    the reset bookings.
    """
    affected = Booking.query.filter(
        Booking.walker_id == walker.id,
        Booking.date >= local_today(),
        Booking.status == 'confirmed',
    ).all()
    reset_bookings_for_lost_availability(affected, actor_id=actor_id, reason=reason)
    return affected
