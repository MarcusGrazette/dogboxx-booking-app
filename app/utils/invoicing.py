"""
Shared invoicing logic used by both admin (invoicing dashboard) and the
client-facing monthly summary page.
"""

from collections import defaultdict
from decimal import Decimal
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


def bill_cancellation_for(booking, *, cancelled_by, today, admin_override=None):
    """Decide the bill_cancellation value to persist at the moment of
    cancellation. Call it BEFORE transition_booking/bulk_transition: it reads
    booking.status as the pre-cancel status, which the transition destroys.

    Always returns an explicit True/False, never None. The None ("legacy")
    branch of is_billable_cancellation() re-derives the decision at invoice
    time from the service type's *live* cancellation_notice_days setting, so
    changing that setting would silently re-bill every past cancellation that
    relied on it. Deciding once, here, and persisting it keeps issued months
    fixed. Existing None rows are left alone — that branch stays frozen.

    Rules, in order:
      - Never confirmed (`requested`/`waitlisted`, including a booking reset
        there by availability_reset.py because DogBoxx lost the walker):
        False, whatever the notice window or admin_override says. No walker
        time was committed, so a late cancellation disrupts nothing.
      - admin_override (True=bill / False=waive, from an admin's late-fee
        choice): returned as-is. Callers only pass it for a genuinely late
        cancellation — True on a non-late booking would wrongly bill it.
      - Otherwise the same policy the legacy branch applies: bill iff the
        client (not an admin) cancelled inside the notice window, judged
        against `today` (use app.utils.dates.local_today()).
    """
    if booking.status != 'confirmed':
        return False
    if admin_override is not None:
        return admin_override
    return cancelled_by != 'admin' and is_late_cancellation(booking, today)


def group_by_bill_cancellation(bookings, *, cancelled_by, today, admin_override_for=None):
    """Bucket bookings by their bill_cancellation_for() decision, for bulk
    paths — bulk_transition takes one bill_cancellation value per call.

    admin_override_for: optional callable(booking) -> True/False/None, for
    routes whose admin override applies to only some rows (late ones).
    Returns {True: [...], False: [...]} with empty buckets omitted.
    """
    buckets = {}
    for b in bookings:
        override = admin_override_for(b) if admin_override_for else None
        decision = bill_cancellation_for(b, cancelled_by=cancelled_by, today=today,
                                         admin_override=override)
        buckets.setdefault(decision, []).append(b)
    return buckets


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


def drop_rebooked_cancellations(billable_cancels, confirmed):
    """Charge a late-cancelled slot at most once.

    A billable cancellation is dropped when the same dog has a *confirmed*
    booking of the *same service* for the same date + slot — the client
    rebooked, so they pay for the walk, not the fee as well. A different
    service still leaves the fee standing (owner decision: a drop-in is
    priced well below the walk it replaced). A requested/waitlisted rebook
    doesn't count until it's confirmed. Of several billable cancellations
    for one (dog, date, slot, service) — cancel, rebook, cancel late again —
    only the first is kept.

    Applied at invoice time, so it covers legacy NULL rows and past months
    alike; bill_cancellation itself is never rewritten.
    """
    def key(b):
        return (b.dog_id, b.date, b.slot, b.service_type_id)

    taken = {key(b) for b in confirmed}
    kept = []
    for b in sorted(billable_cancels, key=lambda b: (b.date, b.slot, b.id)):
        if key(b) not in taken:
            taken.add(key(b))
            kept.append(b)
    return kept


def invoice_for_client(user_id, month_start, month_end, all_configs):
    """Return invoice data dict for a single client in the given month.

    Billable items:
      - confirmed bookings (walks + drop-ins)
      - cancelled bookings where notice < cancellation_notice_days (from ServiceType.settings),
        minus any rebooked for the same slot (see drop_rebooked_cancellations)

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

    confirmed = [b for b in bookings if b.status == 'confirmed']
    # A cancelled booking is billed when is_billable_cancellation() says so:
    # an explicit admin bill/waive choice wins, otherwise the legacy default
    # (client-initiated late cancels only — closures and admin cancels stay free
    # unless the admin opted to bill at cancel time).
    late_cancels = drop_rebooked_cancellations(
        [b for b in bookings if is_billable_cancellation(b)], confirmed)
    all_billable = confirmed + late_cancels

    # Group walk items keyed by (dog_id, date) so the double-slot discount only
    # fires when the SAME dog is booked AM+PM on the same day. Keying by date
    # alone would over-discount multi-dog households where dog A took the
    # morning and dog B took the afternoon. pricing.build_double_slot_discounts
    # — which renders the discount LINES for this same invoice — must use the
    # identical key, or the lines and this subtotal disagree on the same page.
    #
    # Built from `confirmed` only, not `all_billable`: the discount rewards a
    # dog actually getting both walks. A late-cancelled-but-billed leg is a
    # fee, not a walk — counting it here let a dog with one real walk plus one
    # billed cancellation look like a genuine double slot and undercharge by
    # the discount amount (found 2026-09-17, FEATURES.md #79).
    dog_date_slots = defaultdict(set)
    for b in confirmed:
        if not is_drop_in(b):
            dog_date_slots[(b.dog_id, b.date)].add(b.slot)

    walk_confirmed    = [b for b in confirmed if not is_drop_in(b)]
    drop_in_confirmed = [b for b in confirmed if is_drop_in(b)]

    # Calculate subtotal — kept as Decimal throughout (PricingConfig's
    # Numeric(8,2) columns already hand back Decimal) so a month of line
    # items plus two discount subtractions never accumulates binary float
    # representation error before the final round().
    subtotal = Decimal('0.00')
    for b in all_billable:
        subtotal += unit_price(b, config_for_date(all_configs, b.date))
    for (_dog_id, d), slots in dog_date_slots.items():
        if 'Morning' in slots and 'Afternoon' in slots:
            cfg = config_for_date(all_configs, d)
            if cfg:
                subtotal -= cfg.double_slot_discount

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
