"""Shared reset for confirmed bookings that lose their walker's availability.

Every path that removes or shrinks a walker's availability for a (date, slot)
must reset any confirmed booking still sitting on it — otherwise it stays
'confirmed' in the DB while rendering nowhere on /admin/board (the board
derives its walker columns from availability). See CLAUDE.md "Walker
availability change must reset confirmed bookings" for the full list of
call sites.
"""

import uuid

from app.utils.booking_status import bulk_transition


def reset_bookings_for_lost_availability(bookings, *, actor_id, batch,
                                          transition_batch_id=None, reason=None):
    """Reset `bookings` to requested/unassigned, queuing a booking_reset
    notification per client onto the caller-owned `batch`.

    `reason` is a short human-readable clause (no trailing punctuation, e.g.
    "Alice marked unavailable") stamped onto each reset BookingStatusChange
    row's `notes` — the activity feed uses its presence to file these rows
    under "Availability changes" and to say why the booking was reset,
    instead of a bare "Reset ... to requested". Every call site should pass
    one; omit only for a reset with no availability-change cause to report.

    Does not commit and does not flush `batch` — callers may accumulate
    several calls onto one shared NotificationBatch (e.g. a date range
    processed in a loop) before a single flush + commit.
    """
    if not bookings:
        return
    bulk_transition(bookings, 'requested', actor_id=actor_id, walker_id=None,
                    batch_id=transition_batch_id or uuid.uuid4().hex, notes=reason)
    for b in bookings:
        batch.add(b.user_id, 'booking_reset',
                 dog_name=b.dog.name if b.dog else 'Unknown', slot=b.slot, date=b.date,
                 svc_label='drop-in' if b.service_type and b.service_type.slug == 'drop-in' else 'walk')
