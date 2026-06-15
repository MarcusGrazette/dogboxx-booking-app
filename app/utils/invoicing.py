"""
Shared invoicing logic used by both admin (invoicing dashboard) and the
client-facing monthly summary page.
"""

from collections import defaultdict
from sqlalchemy.orm import joinedload
from app.models import DogOwner, Booking
from app.utils.pricing import (
    config_for_date, is_drop_in, unit_price, weekly_discount_for_walks,
)


def _cancellation_notice_days(booking):
    """Return the late-cancel threshold (days) from the booking's service type settings."""
    try:
        return booking.service_type.settings.get('cancellation_notice_days', 5)
    except (AttributeError, TypeError):
        return 5


def is_late_cancellation(booking, ref_date):
    """True if cancelling `booking` on `ref_date` falls inside its notice window.

    Used by admin cancel routes to decide whether to surface the late-fee
    checkbox; mirrors the day-count the invoice uses (`cancelled_at.date()`).
    """
    return (booking.date - ref_date).days < _cancellation_notice_days(booking)


def is_billable_cancellation(booking):
    """Whether a cancelled booking should appear on the invoice.

    Explicit admin choice (`bill_cancellation` True/False) always wins. Otherwise
    fall back to the legacy default policy — bill iff the client cancelled inside
    the notice window. Keeping the default branch unchanged means already-issued
    invoices (legacy rows where bill_cancellation is NULL) never shift.
    """
    if booking.status != 'cancelled' or booking.cancelled_at is None:
        return False
    if booking.bill_cancellation is not None:
        return booking.bill_cancellation
    return (booking.cancelled_by != 'admin'
            and (booking.date - booking.cancelled_at.date()).days
                < _cancellation_notice_days(booking))


def invoice_for_client(user_id, month_start, month_end, all_configs):
    """Return invoice data dict for a single client in the given month.

    Billable items:
      - confirmed / completed bookings (walks + drop-ins)
      - cancelled bookings where notice < cancellation_notice_days (from ServiceType.settings)

    Pricing:
      - Group walks: price_per_walk; double_slot_discount for same-day AM+PM;
        weekly_discount per walk for weeks with ≥5 confirmed group walks
      - Drop-ins: price_per_drop_in; no double discount; no weekly discount

    Returns None if the user has no primary dog (and therefore no bookings).
    """

    dog_owner_ids = [
        do.dog_id
        for do in DogOwner.query.filter_by(user_id=user_id, role='primary').all()
    ]
    if not dog_owner_ids:
        return None

    bookings = (
        Booking.query
        .options(joinedload(Booking.dog), joinedload(Booking.service_type))
        .filter(
            Booking.dog_id.in_(dog_owner_ids),
            Booking.date >= month_start,
            Booking.date < month_end,
            Booking.status.in_(Booking.INVOICE_STATUSES),
        )
        .order_by(Booking.date, Booking.slot)
        .all()
    )

    confirmed = [b for b in bookings if b.status in ('confirmed', 'completed')]
    # A cancelled booking is billed when is_billable_cancellation() says so:
    # an explicit admin bill/waive choice wins, otherwise the legacy default
    # (client-initiated late cancels only — closures and admin cancels stay free
    # unless the admin opted to bill at cancel time).
    late_cancels = [b for b in bookings if is_billable_cancellation(b)]
    all_billable = confirmed + late_cancels

    # Group walk items keyed by (dog_id, date) so the double-slot discount only
    # fires when the SAME dog is booked AM+PM on the same day. Keying by date
    # alone would over-discount multi-dog households where dog A took the
    # morning and dog B took the afternoon.
    dog_date_slots = defaultdict(set)
    for b in all_billable:
        if not is_drop_in(b):
            dog_date_slots[(b.dog_id, b.date)].add(b.slot)

    walk_confirmed    = [b for b in confirmed if not is_drop_in(b)]
    drop_in_confirmed = [b for b in confirmed if is_drop_in(b)]

    # Calculate subtotal
    subtotal = 0.0
    for b in all_billable:
        subtotal += unit_price(b, config_for_date(all_configs, b.date))
    for (_dog_id, d), slots in dog_date_slots.items():
        if 'Morning' in slots and 'Afternoon' in slots:
            cfg = config_for_date(all_configs, d)
            if cfg:
                subtotal -= float(cfg.double_slot_discount)

    # Weekly discount — confirmed group walks only, ≥5 per ISO week
    weekly_discount_total, weekly_discount_weeks = weekly_discount_for_walks(
        [b.date for b in walk_confirmed], all_configs
    )
    subtotal -= weekly_discount_total

    return {
        'confirmed':              confirmed,
        'late_cancels':           late_cancels,
        'all_billable':           all_billable,
        'total_walks':            len(walk_confirmed),
        'total_drop_ins':         len(drop_in_confirmed),
        'total_cancels':          len(late_cancels),
        'total_billable':         len(all_billable),
        'doubles':                sum(1 for s in dog_date_slots.values()
                                      if 'Morning' in s and 'Afternoon' in s),
        'weekly_discount_total':  round(weekly_discount_total, 2),
        'weekly_discount_weeks':  weekly_discount_weeks,
        'subtotal':               round(subtotal, 2),
    }
