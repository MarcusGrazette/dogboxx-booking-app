"""
Single source of truth for pricing primitives.

Before this module the date→`PricingConfig` lookup (`config_for`) was
copy-pasted in four places and the per-booking unit-price / double-slot /
line-item construction was reimplemented in `invoicing.py`,
`admin.routes.invoicing_detail`, and `client.routes.monthly_summary`. A
pricing rule changed in one place could silently disagree with another — a
correctness risk on a money path. Everything pricing-shaped now lives here.

Pricing rules (unchanged from the original implementations):
  - Group walks: ``price_per_walk``; ``double_slot_discount`` once per dog
    booked AM+PM on the same day; ``weekly_discount`` per walk for ISO weeks
    with ≥5 confirmed group walks.
  - Drop-ins: ``price_per_drop_in``; no double-slot discount; no weekly discount.
"""

from collections import defaultdict
from datetime import date as _date

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
    """Per-booking price from ``config`` — drop-in vs walk. 0.0 if no config."""
    if config is None:
        return 0.0
    return float(config.price_per_drop_in) if is_drop_in(booking) else float(config.price_per_walk)


def build_line_items(all_billable, late_cancel_ids, configs):
    """Per-booking line items for an invoice / monthly-summary view.

    Returns a list of dicts (sorted by date then slot), each:
    ``{booking, unit_price, is_cancel, is_drop_in}``.
    """
    line_items = []
    for b in sorted(all_billable, key=lambda x: (x.date, x.slot)):
        cfg = config_for_date(configs, b.date)
        line_items.append({
            'booking':    b,
            'unit_price': unit_price(b, cfg),
            'is_cancel':  b.id in late_cancel_ids,
            'is_drop_in': is_drop_in(b),
        })
    return line_items


def weekly_discount_for_walks(walk_dates, configs):
    """Weekly ≥5-walk discount for ONE billing group's confirmed group-walk dates.

    A "billing group" is whatever the caller bills as a unit: a single client's
    household for invoices, or one primary owner's dogs for the revenue rollup.
    For each ISO week in which the group has ≥5 confirmed group walks, applies
    ``weekly_discount`` per walk, priced from the config effective on that week's
    Monday.

    ``walk_dates`` is an iterable of ``date`` (one per confirmed group walk;
    drop-ins excluded by the caller). Returns ``(total_discount, week_count)``
    where ``week_count`` is the number of qualifying weeks.

    This is the single source for the weekly rule — both ``invoice_for_client``
    and the admin revenue dashboard call it, so the two can never disagree on
    whether a week qualifies or how much it discounts.
    """
    week_counts = defaultdict(int)
    for d in walk_dates:
        iso_year, iso_week, _ = d.isocalendar()
        week_counts[(iso_year, iso_week)] += 1

    total = 0.0
    weeks = 0
    for (iso_year, iso_week), count in week_counts.items():
        if count >= 5:
            monday = _date.fromisocalendar(iso_year, iso_week, 1)
            cfg = config_for_date(configs, monday)
            if cfg and cfg.weekly_discount:
                total += float(cfg.weekly_discount) * count
                weeks += 1
    return round(total, 2), weeks


def build_double_slot_discounts(all_billable, configs):
    """Double-slot discount rows — one per day a dog has both Morning + Afternoon.

    Group walks only (drop-ins never qualify). Returns ``[{date, amount}, ...]``
    sorted by date, skipping days whose config has a zero/empty discount.

    Note: keyed by date alone (not dog_id), matching the original
    ``invoicing_detail`` / ``monthly_summary`` behaviour. The aggregate
    ``invoice_for_client`` keys by ``(dog_id, date)`` for multi-dog households —
    see that function. The two are reconciled per-client (a household billed as
    one), so the date-only keying here is correct for the per-client views.
    """
    date_slots = defaultdict(set)
    for b in all_billable:
        if not is_drop_in(b):
            date_slots[b.date].add(b.slot)

    discounts = []
    for d in sorted(d for d, slots in date_slots.items()
                    if 'Morning' in slots and 'Afternoon' in slots):
        cfg = config_for_date(configs, d)
        if cfg and cfg.double_slot_discount:
            discounts.append({'date': d, 'amount': float(cfg.double_slot_discount)})
    return discounts
