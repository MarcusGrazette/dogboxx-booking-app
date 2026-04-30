"""
Shared invoicing logic used by both admin (invoicing dashboard) and the
client-facing monthly summary page.
"""

from collections import defaultdict
from datetime import date as _date
from sqlalchemy.orm import joinedload
from app.models import DogOwner, Booking, ServiceType


def _cancellation_notice_days(booking):
    """Return the late-cancel threshold (days) from the booking's service type settings."""
    try:
        return booking.service_type.settings.get('cancellation_notice_days', 5)
    except (AttributeError, TypeError):
        return 5


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

    def config_for(d):
        for c in all_configs:
            if c.effective_from <= d:
                return c
        return None

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
    late_cancels = [
        b for b in bookings
        if b.status == 'cancelled'
        and b.cancelled_at is not None
        and (b.date - b.cancelled_at.date()).days < _cancellation_notice_days(b)
    ]
    all_billable = confirmed + late_cancels

    def is_drop_in(b):
        return b.service_type and b.service_type.slug == ServiceType.DROP_IN

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
        cfg = config_for(b.date)
        if cfg:
            if is_drop_in(b):
                subtotal += float(cfg.price_per_drop_in)
            else:
                subtotal += float(cfg.price_per_walk)
    for (_dog_id, d), slots in dog_date_slots.items():
        if 'Morning' in slots and 'Afternoon' in slots:
            cfg = config_for(d)
            if cfg:
                subtotal -= float(cfg.double_slot_discount)

    # Weekly discount — confirmed group walks only, ≥5 per ISO week
    week_walks: dict = defaultdict(int)
    for b in walk_confirmed:
        iso_year, iso_week, _ = b.date.isocalendar()
        week_walks[(iso_year, iso_week)] += 1

    weekly_discount_total = 0.0
    weekly_discount_weeks = 0
    for (iso_year, iso_week), count in week_walks.items():
        if count >= 5:
            rep_date = _date.fromisocalendar(iso_year, iso_week, 1)  # Monday of the week
            cfg = config_for(rep_date)
            if cfg and cfg.weekly_discount:
                weekly_discount_total += float(cfg.weekly_discount) * count
                weekly_discount_weeks += 1
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
