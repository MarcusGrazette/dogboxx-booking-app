"""
Shared invoicing logic used by both admin (invoicing dashboard) and the
client-facing monthly summary page.
"""

from collections import defaultdict
from sqlalchemy.orm import joinedload
from app.models import DogOwner, Booking


def invoice_for_client(user_id, month_start, month_end, all_configs):
    """Return invoice data dict for a single client in the given month.

    Billable items:
      - confirmed / completed bookings (walks + drop-ins)
      - cancelled bookings where notice < 5 days  (booking.date - cancelled_at.date() < 5)

    Pricing:
      - Group walks: price_per_walk; double_slot_discount for same-day AM+PM
      - Drop-ins: price_per_drop_in; no double discount

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
            Booking.status.in_(['confirmed', 'completed', 'cancelled']),
        )
        .order_by(Booking.date, Booking.slot)
        .all()
    )

    confirmed = [b for b in bookings if b.status in ('confirmed', 'completed')]
    late_cancels = [
        b for b in bookings
        if b.status == 'cancelled'
        and b.cancelled_at is not None
        and (b.date - b.cancelled_at.date()).days < 5
    ]
    all_billable = confirmed + late_cancels

    def is_drop_in(b):
        return b.service_type and b.service_type.slug == 'drop-in'

    # Group walk items by date → slot set (for double-slot discount)
    date_slots = defaultdict(set)
    for b in all_billable:
        if not is_drop_in(b):
            date_slots[b.date].add(b.slot)

    # Calculate subtotal
    subtotal = 0.0
    for b in all_billable:
        cfg = config_for(b.date)
        if cfg:
            if is_drop_in(b):
                subtotal += float(cfg.price_per_drop_in)
            else:
                subtotal += float(cfg.price_per_walk)
    for d, slots in date_slots.items():
        if 'Morning' in slots and 'Afternoon' in slots:
            cfg = config_for(d)
            if cfg:
                subtotal -= float(cfg.double_slot_discount)

    walk_confirmed    = [b for b in confirmed if not is_drop_in(b)]
    drop_in_confirmed = [b for b in confirmed if is_drop_in(b)]

    return {
        'confirmed':      confirmed,
        'late_cancels':   late_cancels,
        'all_billable':   all_billable,
        'total_walks':    len(walk_confirmed),
        'total_drop_ins': len(drop_in_confirmed),
        'total_cancels':  len(late_cancels),
        'total_billable': len(all_billable),
        'doubles':        sum(1 for s in date_slots.values()
                              if 'Morning' in s and 'Afternoon' in s),
        'subtotal':       round(subtotal, 2),
    }
