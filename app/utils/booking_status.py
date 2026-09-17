"""Booking status transition chokepoint (NOTIFICATIONS.md §9.3, Session 1).

Every booking status change in the app must route through this module. That is
the single load-bearing invariant (principle P1): you cannot change a booking's
status without also appending a `BookingStatusChange` (BSC) row, so the action
log can never silently drift out of sync with reality again (the §8.2 gap).

These helpers mutate the booking and queue the BSC row on the session; they do
**not** commit. The caller keeps ownership of the transaction (notifications and
other side effects commit atomically alongside the status change).

This module deliberately does no notifying — notifications are emitted by the
callers (and will be unified in Session 2). Session 1 is log-only, no behaviour
change.
"""

from datetime import datetime, timezone

from app.models import db, BookingStatusChange

# Sentinel so callers can distinguish "don't touch this field" from "set it to
# None" (e.g. walker_id=None means unassign, walker_id=_UNSET means leave it).
_UNSET = object()


class InvalidTransitionError(Exception):
    """Raised by transition_booking when the caller passed allowed_from and
    the booking's current status isn't in it — e.g. a stale admin board
    trying to confirm a booking a client cancelled after the board loaded.
    Callers decide the response; typically a 409 telling the actor to reload.
    """
    def __init__(self, from_status, allowed_from):
        self.from_status = from_status
        self.allowed_from = allowed_from
        super().__init__(
            f"Cannot transition from {from_status!r}; allowed: {sorted(allowed_from)}"
        )


def transition_booking(booking, to_status, *, actor_id, notes=None,
                       walker_id=_UNSET, cancelled_by=_UNSET, batch_id=None,
                       old_slot=None, new_slot=None, bill_cancellation=_UNSET,
                       allowed_from=None):
    """Mutate a booking's status and append a BookingStatusChange row.

    Sets confirmed_at / cancelled_at as implied by to_status. cancelled_by is
    not derivable from the status alone (it records client vs admin), so pass it
    explicitly on cancel/reject paths that need it. If walker_id is passed,
    updates it (None to unassign). Pass bill_cancellation (True=bill /
    False=waive / None=default policy) on admin cancel paths to override the
    late-cancel billing default — see app/utils/invoicing.py. Pass old_slot/
    new_slot on slot-override re-confirms so the activity feed can detect moves
    structurally. The BSC row always snapshots booking.walker_id *after* any
    mutation above, so historical reads (e.g. the activity feed) see who was
    assigned as of this exact transition, not whoever holds the booking now.

    allowed_from (optional set of statuses): when given, raises
    InvalidTransitionError if booking.status isn't in it, instead of mutating.
    Opt-in — omitted by the bulk paths (closures, bulk-cancel, availability
    reset) whose callers already pre-filter by status, so a resubmitted row
    already in the target state doesn't start erroring. Pass it from routes
    that accept an arbitrary booking_id from a client or a possibly-stale
    admin board, where a from-state check closes a real race/replay gap.

    Returns the BSC row. Caller still commits.
    """
    from_status = booking.status
    if allowed_from is not None and from_status not in allowed_from:
        raise InvalidTransitionError(from_status, allowed_from)
    now = datetime.now(timezone.utc)

    booking.status = to_status
    if to_status == 'confirmed':
        booking.confirmed_at = now
    if to_status in ('cancelled', 'rejected'):
        booking.cancelled_at = now
    if cancelled_by is not _UNSET:
        booking.cancelled_by = cancelled_by
    if bill_cancellation is not _UNSET:
        booking.bill_cancellation = bill_cancellation
    if walker_id is not _UNSET:
        booking.walker_id = walker_id

    bsc = BookingStatusChange(
        booking=booking,
        from_status=from_status,
        to_status=to_status,
        changed_by_id=actor_id,
        notes=notes,
        old_slot=old_slot,
        new_slot=new_slot,
        walker_id=booking.walker_id,
        batch_id=batch_id,
    )
    db.session.add(bsc)
    return bsc


def record_booking_created(booking, *, actor_id, batch_id=None):
    """First BSC row for a new booking: from_status=None, to_status=current.

    Call this once the booking row exists with its initial status (requested /
    waitlisted). A subsequent auto-confirm is a separate transition_booking row.
    """
    bsc = BookingStatusChange(
        booking=booking,
        from_status=None,
        to_status=booking.status,
        changed_by_id=actor_id,
        notes=None,
        walker_id=booking.walker_id,
        batch_id=batch_id,
    )
    db.session.add(bsc)
    return bsc


def bulk_transition(bookings, to_status, *, actor_id, notes=None,
                    walker_id=_UNSET, cancelled_by=_UNSET, batch_id=None,
                    bill_cancellation=_UNSET):
    """Transition many bookings, one BSC row each.

    Replaces the raw bulk `.update()` calls so each affected row is logged.
    Pass a shared batch_id (uuid4().hex) so the feed can cluster the action.
    Returns the list of BSC rows.
    """
    return [
        transition_booking(
            b, to_status, actor_id=actor_id, notes=notes,
            walker_id=walker_id, cancelled_by=cancelled_by, batch_id=batch_id,
            bill_cancellation=bill_cancellation,
        )
        for b in bookings
    ]
