"""Unit tests for app/utils/pricing.py — the shared pricing primitives.

These functions are pure, so the tests use lightweight stubs instead of DB
fixtures. Behavioural coverage of the full invoice path lives in
test_invoicing.py; this file pins the extracted primitives so the three call
sites (invoicing, admin revenue/detail, client monthly summary) can never drift.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models import ServiceType
from app.utils.pricing import (
    config_for_date,
    is_drop_in,
    unit_price,
    build_line_items,
    build_double_slot_discounts,
    weekly_discount_for_walks,
)


def _cfg(effective_from, walk=10.0, drop_in=6.0, double=2.0):
    # Decimal, not float: real PricingConfig columns are Numeric(8,2), so
    # SQLAlchemy always hands back Decimal — these stubs mirror that (audit
    # M15: pricing.py is Decimal end-to-end and would TypeError against a
    # float stand-in).
    return SimpleNamespace(
        effective_from=effective_from,
        price_per_walk=Decimal(str(walk)),
        price_per_drop_in=Decimal(str(drop_in)),
        double_slot_discount=Decimal(str(double)),
    )


def _booking(bid, d, slot, slug=ServiceType.WALK, dog_id=1):
    return SimpleNamespace(
        id=bid, dog_id=dog_id, date=d, slot=slot,
        service_type=SimpleNamespace(slug=slug),
    )


# ── config_for_date ────────────────────────────────────────────────────────

class TestConfigForDate:
    def test_returns_most_recent_effective_config(self):
        configs = [  # descending by effective_from, as every call site queries
            _cfg(date(2026, 6, 1), walk=12),
            _cfg(date(2026, 1, 1), walk=10),
        ]
        assert config_for_date(configs, date(2026, 6, 15)).price_per_walk == 12
        assert config_for_date(configs, date(2026, 3, 1)).price_per_walk == 10

    def test_returns_none_before_all_configs(self):
        configs = [_cfg(date(2026, 1, 1))]
        assert config_for_date(configs, date(2025, 12, 31)) is None

    def test_boundary_date_inclusive(self):
        configs = [_cfg(date(2026, 6, 1), walk=12)]
        assert config_for_date(configs, date(2026, 6, 1)).price_per_walk == 12

    def test_empty_configs(self):
        assert config_for_date([], date(2026, 6, 1)) is None


# ── is_drop_in / unit_price ────────────────────────────────────────────────

class TestUnitPricing:
    def test_is_drop_in_true_for_drop_in(self):
        assert is_drop_in(_booking(1, date(2026, 6, 1), 'Morning', ServiceType.DROP_IN))

    def test_is_drop_in_false_for_walk(self):
        assert not is_drop_in(_booking(1, date(2026, 6, 1), 'Morning'))

    def test_is_drop_in_false_when_no_service_type(self):
        assert not is_drop_in(SimpleNamespace(service_type=None))

    def test_unit_price_walk(self):
        b = _booking(1, date(2026, 6, 1), 'Morning')
        assert unit_price(b, _cfg(date(2026, 1, 1), walk=10)) == 10.0

    def test_unit_price_drop_in(self):
        b = _booking(1, date(2026, 6, 1), 'Morning', ServiceType.DROP_IN)
        assert unit_price(b, _cfg(date(2026, 1, 1), drop_in=6)) == 6.0

    def test_unit_price_zero_when_no_config(self):
        b = _booking(1, date(2026, 6, 1), 'Morning')
        assert unit_price(b, None) == 0.0


# ── build_line_items ───────────────────────────────────────────────────────

class TestBuildLineItems:
    def test_sorted_and_priced(self):
        configs = [_cfg(date(2026, 1, 1), walk=10, drop_in=6)]
        b_pm = _booking(2, date(2026, 6, 2), 'Afternoon')
        b_am = _booking(1, date(2026, 6, 1), 'Morning', ServiceType.DROP_IN)
        items = build_line_items([b_pm, b_am], late_cancel_ids={2}, configs=configs)
        # sorted by (date, slot): the drop-in on Jun 1 comes first
        assert [li['booking'].id for li in items] == [1, 2]
        assert items[0]['unit_price'] == 6.0 and items[0]['is_drop_in'] is True
        assert items[1]['unit_price'] == 10.0 and items[1]['is_cancel'] is True

    def test_empty(self):
        assert build_line_items([], set(), []) == []


# ── build_double_slot_discounts ────────────────────────────────────────────

class TestDoubleSlotDiscounts:
    def test_discount_when_same_day_am_and_pm(self):
        configs = [_cfg(date(2026, 1, 1), double=2.5)]
        am = _booking(1, date(2026, 6, 1), 'Morning')
        pm = _booking(2, date(2026, 6, 1), 'Afternoon')
        out = build_double_slot_discounts([am, pm], configs)
        assert out == [{'date': date(2026, 6, 1), 'amount': 2.5}]

    def test_no_discount_for_single_slot(self):
        configs = [_cfg(date(2026, 1, 1), double=2.5)]
        am = _booking(1, date(2026, 6, 1), 'Morning')
        assert build_double_slot_discounts([am], configs) == []

    def test_drop_ins_never_qualify(self):
        configs = [_cfg(date(2026, 1, 1), double=2.5)]
        am = _booking(1, date(2026, 6, 1), 'Morning', ServiceType.DROP_IN)
        pm = _booking(2, date(2026, 6, 1), 'Afternoon', ServiceType.DROP_IN)
        assert build_double_slot_discounts([am, pm], configs) == []

    def test_skipped_when_config_discount_zero(self):
        configs = [_cfg(date(2026, 1, 1), double=0)]
        am = _booking(1, date(2026, 6, 1), 'Morning')
        pm = _booking(2, date(2026, 6, 1), 'Afternoon')
        assert build_double_slot_discounts([am, pm], configs) == []

    def test_no_discount_when_two_dogs_split_the_slots(self):
        """Multi-dog household, dog A in the AM and dog B in the PM: neither dog
        is on a double slot, so no row. Keying by date alone produced a phantom
        discount line here that invoice_for_client never billed."""
        configs = [_cfg(date(2026, 1, 1), double=2.5)]
        am = _booking(1, date(2026, 6, 1), 'Morning',   dog_id=101)
        pm = _booking(2, date(2026, 6, 1), 'Afternoon', dog_id=102)
        assert build_double_slot_discounts([am, pm], configs) == []

    def test_one_row_when_only_one_dog_has_both_slots(self):
        configs = [_cfg(date(2026, 1, 1), double=2.5)]
        bookings = [
            _booking(1, date(2026, 6, 1), 'Morning',   dog_id=101),
            _booking(2, date(2026, 6, 1), 'Afternoon', dog_id=101),
            _booking(3, date(2026, 6, 1), 'Morning',   dog_id=102),
        ]
        out = build_double_slot_discounts(bookings, configs)
        assert out == [{'date': date(2026, 6, 1), 'amount': 2.5}]

    def test_one_row_per_dog_when_both_dogs_have_both_slots(self):
        """Two rows on one date — the templates render a line per row and the
        week subtotals sum the amounts, so 2 x the discount is applied."""
        configs = [_cfg(date(2026, 1, 1), double=2.5)]
        bookings = [
            _booking(1, date(2026, 6, 1), 'Morning',   dog_id=101),
            _booking(2, date(2026, 6, 1), 'Afternoon', dog_id=101),
            _booking(3, date(2026, 6, 1), 'Morning',   dog_id=102),
            _booking(4, date(2026, 6, 1), 'Afternoon', dog_id=102),
        ]
        out = build_double_slot_discounts(bookings, configs)
        assert out == [
            {'date': date(2026, 6, 1), 'amount': 2.5},
            {'date': date(2026, 6, 1), 'amount': 2.5},
        ]

    def test_rows_sorted_by_date_regardless_of_input_order(self):
        configs = [_cfg(date(2026, 1, 1), double=2.5)]
        bookings = [
            _booking(1, date(2026, 6, 3), 'Afternoon', dog_id=102),
            _booking(2, date(2026, 6, 1), 'Morning',   dog_id=101),
            _booking(3, date(2026, 6, 3), 'Morning',   dog_id=102),
            _booking(4, date(2026, 6, 1), 'Afternoon', dog_id=101),
        ]
        out = build_double_slot_discounts(bookings, configs)
        assert [r['date'] for r in out] == [date(2026, 6, 1), date(2026, 6, 3)]


# ── weekly_discount_for_walks ──────────────────────────────────────────────

def _cfg_weekly(effective_from, weekly):
    c = _cfg(effective_from)
    c.weekly_discount = Decimal(str(weekly))
    return c


class TestWeeklyDiscount:
    # Mon 2026-06-01 .. Fri 2026-06-05 is one ISO week (5 walks).
    WEEK = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3),
            date(2026, 6, 4), date(2026, 6, 5)]

    def test_five_walks_one_week_qualifies(self):
        configs = [_cfg_weekly(date(2026, 1, 1), weekly=1.5)]
        total, weeks = weekly_discount_for_walks(self.WEEK, configs)
        assert total == 7.5 and weeks == 1   # 1.5 * 5 walks

    def test_four_walks_does_not_qualify(self):
        configs = [_cfg_weekly(date(2026, 1, 1), weekly=1.5)]
        total, weeks = weekly_discount_for_walks(self.WEEK[:4], configs)
        assert total == 0.0 and weeks == 0

    def test_two_qualifying_weeks(self):
        configs = [_cfg_weekly(date(2026, 1, 1), weekly=1.0)]
        next_week = [d.replace(day=d.day + 7) for d in self.WEEK]
        total, weeks = weekly_discount_for_walks(self.WEEK + next_week, configs)
        assert total == 10.0 and weeks == 2   # 1.0 * 5 * 2 weeks

    def test_zero_when_config_weekly_is_zero(self):
        configs = [_cfg_weekly(date(2026, 1, 1), weekly=0)]
        total, weeks = weekly_discount_for_walks(self.WEEK, configs)
        assert total == 0.0 and weeks == 0

    def test_empty_dates(self):
        configs = [_cfg_weekly(date(2026, 1, 1), weekly=1.5)]
        assert weekly_discount_for_walks([], configs) == (0.0, 0)
