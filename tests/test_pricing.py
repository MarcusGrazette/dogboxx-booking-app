"""Unit tests for app/utils/pricing.py — the shared pricing primitives.

These functions are pure, so the tests use lightweight stubs instead of DB
fixtures. Behavioural coverage of the full invoice path lives in
test_invoicing.py; this file pins the extracted primitives so the three call
sites (invoicing, admin revenue/detail, client monthly summary) can never drift.
"""

from datetime import date
from types import SimpleNamespace

from app.models import ServiceType
from app.utils.pricing import (
    config_for_date,
    is_drop_in,
    unit_price,
    build_line_items,
    build_double_slot_discounts,
)


def _cfg(effective_from, walk=10.0, drop_in=6.0, double=2.0):
    return SimpleNamespace(
        effective_from=effective_from,
        price_per_walk=walk,
        price_per_drop_in=drop_in,
        double_slot_discount=double,
    )


def _booking(bid, d, slot, slug=ServiceType.WALK):
    return SimpleNamespace(
        id=bid, date=d, slot=slot,
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
