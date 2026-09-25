"""
Single source of truth for pricing primitives.

Before this module the date→`PricingConfig` lookup (`config_for`) was
copy-pasted in four places and the per-booking unit-price / double-slot /
line-item construction was reimplemented in `invoicing.py`,
`admin.views.invoicing.invoicing_detail`, and
`client.views.profile.monthly_summary`. A
pricing rule changed in one place could silently disagree with another — a
correctness risk on a money path. Everything pricing-shaped now lives here.

Pricing rules:
  - Group walks: ``price_per_walk``; ``double_slot_discount`` once per dog
    booked AM+PM on the same day; ``weekly_discount`` per walk for ISO weeks
    with ≥5 confirmed group walks.
  - Drop-ins: ``price_per_drop_in``; no double-slot discount; no weekly discount.

The extraction originally preserved each call site's behaviour verbatim, which
carried one real divergence across: ``build_double_slot_discounts`` grouped by
date alone while ``invoice_for_client`` grouped by ``(dog_id, date)``, so a
multi-dog household with dog A in the AM and dog B in the PM saw a discount line
on the invoice detail page that the billed subtotal never included. Both now key
by ``(dog_id, date)``. When adding a rule here, the aggregation key matters as
much as the arithmetic — that is where the two implementations drifted.
"""

from collections import defaultdict
from datetime import date as _date
from decimal import Decimal

from app.models import ServiceType


def config_for_date(configs, d):
    """Return the effective ``PricingConfig`` for date ``d``.

    ``configs`` MUST be ordered by ``effective_from`` descending (every call
    site already queries it that way). Returns the first config whose
    ``effective_from`` is on or before ``d``, or ``None`` if ``d`` predates all
    configured pricing.
    """
    for c in configs:
        if c.effective_from <= d:
            return c
    return None


def is_drop_in(booking):
    """True if ``booking`` is a drop-in (vs a group walk)."""
    return bool(booking.service_type and booking.service_type.slug == ServiceType.DROP_IN)


def unit_price(booking, config):
    """Per-booking price from ``config`` — drop-in vs walk. ``Decimal('0.00')``
    if no config. Kept as Decimal (PricingConfig's Numeric(8,2) columns already
    hand back Decimal — this just stops the float() round-trip) so invoice
    totals never accumulate binary float error across a month of line items."""
    if config is None:
        return Decimal('0.00')
    return config.price_per_drop_in if is_drop_in(booking) else config.price_per_walk


# Day order for line items. Sorting the slot strings put Afternoon before
# Morning. Other booking_slot values (Full Day / Half Day AM|PM) belong to the
# unoffered day-care service — no order is defined for them, so they sort last.
SLOT_ORDER = {'Morning': 0, 'Afternoon': 1}


def build_line_items(all_billable, late_cancel_ids, configs):
    """Per-booking line items for an invoice / monthly-summary view.

    Returns a list of dicts (sorted by date then slot, Morning first), each:
    ``{booking, unit_price, is_cancel, is_drop_in}``.
    """
    line_items = []
    for b in sorted(all_billable,
                    key=lambda x: (x.date, SLOT_ORDER.get(x.slot, len(SLOT_ORDER)))):
        cfg = config_for_date(configs, b.date)
        line_items.append({
            'booking':    b,
            'unit_price': unit_price(b, cfg),
            'is_cancel':  b.id in late_cancel_ids,
            'is_drop_in': is_drop_in(b),
        })
    return line_items


def build_weekly_discounts(walk_dates, configs):
    """Weekly ≥5-walk discount rows for ONE billing group's confirmed group walks.

    A "billing group" is whatever the caller bills as a unit: a single client's
    household for invoices, or one primary owner's dogs for the revenue rollup.
    For each ISO week in which the group has ≥5 confirmed group walks, applies
    ``weekly_discount`` per walk, priced from the config effective on that week's
    Monday. A week with no configured discount gets no row.

    ``walk_dates`` is an iterable of ``date`` (one per confirmed group walk;
    drop-ins excluded by the caller). Returns a date-sorted list of
    ``{week_start, week_end, walk_count, amount}`` (Mon, Sun inclusive).

    This is the single source for the weekly rule: ``weekly_discount_for_walks``
    sums these rows, ``invoice_for_client`` returns them for the admin invoice
    detail and the client monthly summary to render.
    """
    week_counts = defaultdict(int)
    for d in walk_dates:
        iso_year, iso_week, _ = d.isocalendar()
        week_counts[(iso_year, iso_week)] += 1

    rows = []
    for (iso_year, iso_week), count in sorted(week_counts.items()):
        if count >= 5:
            monday = _date.fromisocalendar(iso_year, iso_week, 1)
            cfg = config_for_date(configs, monday)
            if cfg and cfg.weekly_discount:
                rows.append({
                    'week_start': monday,
                    'week_end':   _date.fromisocalendar(iso_year, iso_week, 7),
                    'walk_count': count,
                    'amount':     round(cfg.weekly_discount * count, 2),
                })
    return rows


def weekly_discount_for_walks(walk_dates, configs):
    """``(total_discount, week_count)`` for ``build_weekly_discounts``' rows —
    the totals-only form the revenue dashboard uses."""
    rows = build_weekly_discounts(walk_dates, configs)
    return round(sum((r['amount'] for r in rows), Decimal('0.00')), 2), len(rows)


def build_double_slot_discounts(confirmed_bookings, configs):
    """Double-slot discount rows — one per ``(dog, day)`` where that dog has
    both Morning + Afternoon.

    Group walks only (drop-ins never qualify). Returns ``[{date, amount}, ...]``
    sorted by date, skipping days whose config has a zero/empty discount.

    Keyed by ``(dog_id, date)`` to match ``invoice_for_client``'s subtotal: the
    discount rewards ONE dog doing two slots, so a multi-dog household with dog
    A in the AM and dog B in the PM has no dog on a double slot and gets no
    discount. Conversely a household where two dogs each do AM+PM gets two rows,
    matching ``inv['doubles'] == 2``. The rows carry only the date (the invoice
    templates render per-day lines), but multiple rows can share one date.

    ``confirmed_bookings`` must be *confirmed-only* (pass ``inv['confirmed']``,
    never ``inv['all_billable']``) — a late-cancelled-but-billed leg is a fee,
    not a walk, and counting it toward eligibility here previously let a dog
    with one real walk plus one billed cancellation look like a genuine double
    slot and undercharge by the discount amount (found 2026-09-17, FEATURES.md
    #79).
    """
    dog_date_slots = defaultdict(set)
    for b in confirmed_bookings:
        if not is_drop_in(b):
            dog_date_slots[(b.dog_id, b.date)].add(b.slot)

    discounts = []
    qualifying = (k for k, slots in dog_date_slots.items()
                  if 'Morning' in slots and 'Afternoon' in slots)
    # Sort by date, then dog_id — same-day rows are visually identical, but a
    # total order keeps the output independent of booking iteration order.
    for _dog_id, d in sorted(qualifying, key=lambda k: (k[1], k[0])):
        cfg = config_for_date(configs, d)
        if cfg and cfg.double_slot_discount:
            discounts.append({'date': d, 'amount': cfg.double_slot_discount})
    return discounts
