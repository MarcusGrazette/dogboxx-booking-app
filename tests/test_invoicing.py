"""
Invoicing tests — _invoice_for_client() helper.

Covers:
- No primary dogs → returns None
- Group walks only: correct subtotal and total_walks count
- Drop-ins only: correct subtotal (price_per_drop_in), total_drop_ins count
- Mixed walks + drop-ins: both counted, totals correct
- Same-day AM+PM group walks → double-slot discount applied
- Drop-ins on same day as group walks → no double discount for drop-in pair
- Late cancel (< 5 days notice) → billable
- Early cancel (>= 5 days notice) → not billable
- Cancel with no cancelled_at → not billable
- total_walks / total_drop_ins / total_cancels counts
- Weekly discount: ≥5 confirmed group walks in ISO week → per-walk discount
- Weekly discount: <5 walks → no discount
- Weekly discount: drop-ins do not count toward threshold or receive discount
- Weekly discount + double-slot discount are cumulative
"""
import datetime
import pytest
from werkzeug.security import generate_password_hash
from decimal import Decimal
from sqlalchemy import text

from app import db
from app.models import (
    User, Client, Dog, DogOwner, Walker, ServiceType,
    Booking, PricingConfig,
)
from app.utils.invoicing import invoice_for_client as _invoice_for_client


# ---------------------------------------------------------------------------
# Truncation fixture
# ---------------------------------------------------------------------------

TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'walker_schedules', 'walker_unavailabilities',
    'walkers', 'clients', 'pricing_configs', 'service_types', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield


# ---------------------------------------------------------------------------
# Date constants — fixed in the past so no validation concerns
# ---------------------------------------------------------------------------

MONTH_START = datetime.date(2026, 2, 1)
MONTH_END   = datetime.date(2026, 3, 1)   # exclusive

MON_1  = datetime.date(2026, 2, 2)   # Monday  — within month
MON_2  = datetime.date(2026, 2, 9)   # Monday  — within month
TUE_1  = datetime.date(2026, 2, 3)   # Tuesday — within month

# A pricing config effective well before our test dates
PRICE_DATE = datetime.date(2025, 1, 1)
WALK_PRICE       = 12.00
DROP_IN_PRICE    =  5.00
DOUBLE_DISCOUNT  =  2.00
WEEKLY_DISCOUNT  =  1.00


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='client'):
    u = User(
        firstname='Test', lastname='User', email=email,
        role=role, is_admin=False,
        hashed_password=generate_password_hash('Testpass1!'),
        active=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def make_client_with_dog(email):
    u = make_user(email)
    db.session.add(Client(user_id=u.id, onboarding_completed=True))
    dog = Dog(name='TestDog', breed='Mutt')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=u.id, role='primary'))
    db.session.flush()
    return u, dog


def make_pricing_config(
    effective_from=PRICE_DATE,
    price_per_walk=WALK_PRICE,
    price_per_drop_in=DROP_IN_PRICE,
    double_slot_discount=DOUBLE_DISCOUNT,
    weekly_discount=0.00,
):
    cfg = PricingConfig(
        effective_from=effective_from,
        price_per_walk=price_per_walk,
        price_per_drop_in=price_per_drop_in,
        double_slot_discount=double_slot_discount,
        weekly_discount=weekly_discount,
    )
    db.session.add(cfg)
    db.session.flush()
    return cfg


def make_walk_service():
    st = ServiceType(
        name='Group Walk', slug='group-walk',
        capacity_model='walker_assigned', slot_type='morning_afternoon',
        requires_walker=True, default_max_capacity=6, active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


def make_drop_in_service():
    st = ServiceType(
        name='Drop In', slug='drop-in',
        capacity_model='walker_assigned', slot_type='morning_afternoon',
        requires_walker=True, default_max_capacity=6, active=True,
        settings={},
    )
    db.session.add(st)
    db.session.flush()
    return st


def add_booking(user, dog, service, date, slot, status='confirmed',
                cancelled_at=None, cancelled_by=None, bill_cancellation=None):
    b = Booking(
        user_id=user.id, dog_id=dog.id,
        service_type_id=service.id,
        date=date, slot=slot, status=status,
        cancelled_at=cancelled_at,
        cancelled_by=cancelled_by,
        bill_cancellation=bill_cancellation,
    )
    db.session.add(b)
    db.session.flush()
    return b


def all_configs():
    """Return PricingConfig rows sorted desc by effective_from (matches route pattern)."""
    return (
        PricingConfig.query
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInvoiceForClient:

    def test_no_primary_dogs_returns_none(self, app):
        """Client with no primary dog ownership → None."""
        with app.app_context():
            u = make_user('inv_nodogs@test.com')
            make_pricing_config()
            db.session.commit()
            result = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert result is None

    def test_no_bookings_returns_zero_subtotal(self, app):
        with app.app_context():
            u, dog = make_client_with_dog('inv_empty@test.com')
            make_pricing_config()
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv is not None
            assert inv['subtotal'] == 0.0
            assert inv['total_walks'] == 0
            assert inv['total_drop_ins'] == 0
            assert inv['total_cancels'] == 0

    def test_single_group_walk_subtotal(self, app):
        with app.app_context():
            u, dog = make_client_with_dog('inv_walk@test.com')
            st = make_walk_service()
            make_pricing_config()
            add_booking(u, dog, st, MON_1, 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 1
            assert inv['total_drop_ins'] == 0
            assert inv['subtotal'] == WALK_PRICE

    def test_single_drop_in_subtotal(self, app):
        with app.app_context():
            u, dog = make_client_with_dog('inv_di@test.com')
            st = make_drop_in_service()
            make_pricing_config()
            add_booking(u, dog, st, MON_1, 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_drop_ins'] == 1
            assert inv['total_walks'] == 0
            assert inv['subtotal'] == DROP_IN_PRICE

    def test_mixed_walks_and_drop_ins(self, app):
        with app.app_context():
            u, dog = make_client_with_dog('inv_mixed@test.com')
            walk_st = make_walk_service()
            di_st   = make_drop_in_service()
            make_pricing_config()
            add_booking(u, dog, walk_st, MON_1, 'Morning', status='confirmed')
            add_booking(u, dog, di_st,   MON_2, 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 1
            assert inv['total_drop_ins'] == 1
            assert inv['subtotal'] == round(WALK_PRICE + DROP_IN_PRICE, 2)

    def test_double_slot_discount_applied_to_am_pm_walks(self, app):
        """AM + PM group walks on the same day → double-slot discount."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_dbl@test.com')
            st = make_walk_service()
            make_pricing_config()
            add_booking(u, dog, st, MON_1, 'Morning',   status='confirmed')
            add_booking(u, dog, st, MON_1, 'Afternoon', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            expected = round(WALK_PRICE * 2 - DOUBLE_DISCOUNT, 2)
            assert inv['doubles'] == 1
            assert inv['subtotal'] == expected

    def test_double_slot_discount_not_applied_to_drop_ins(self, app):
        """AM + PM drop-ins on the same day → no double-slot discount."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_di_nodbl@test.com')
            st = make_drop_in_service()
            make_pricing_config()
            add_booking(u, dog, st, MON_1, 'Morning',   status='confirmed')
            add_booking(u, dog, st, MON_1, 'Afternoon', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            expected = round(DROP_IN_PRICE * 2, 2)
            assert inv['doubles'] == 0
            assert inv['subtotal'] == expected

    def test_mixed_same_day_walk_and_drop_in_no_discount(self, app):
        """AM group walk + PM drop-in on same day — drop-in doesn't form a double-slot pair."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_mix_sameday@test.com')
            walk_st = make_walk_service()
            di_st   = make_drop_in_service()
            make_pricing_config()
            # Morning walk + Afternoon drop-in — different slots, different service types
            add_booking(u, dog, walk_st, MON_1, 'Morning',   status='confirmed')
            add_booking(u, dog, di_st,   MON_1, 'Afternoon', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            # Only one group walk slot tracked → no double discount triggered
            assert inv['doubles'] == 0
            assert inv['subtotal'] == round(WALK_PRICE + DROP_IN_PRICE, 2)

    def test_double_slot_discount_not_applied_across_different_dogs(self, app):
        """Multi-dog household: dog A took the AM slot, dog B took the PM slot
        on the same day. Neither dog has both slots, so no discount applies."""
        with app.app_context():
            u, dog_a = make_client_with_dog('inv_multidog_split@test.com')
            dog_b = Dog(name='SecondDog', breed='Mutt')
            db.session.add(dog_b)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog_b.id, user_id=u.id, role='primary'))
            st = make_walk_service()
            make_pricing_config()
            add_booking(u, dog_a, st, MON_1, 'Morning',   status='confirmed')
            add_booking(u, dog_b, st, MON_1, 'Afternoon', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 2
            assert inv['doubles'] == 0
            assert inv['subtotal'] == round(WALK_PRICE * 2, 2)

    def test_double_slot_discount_per_dog_when_one_dog_has_both_slots(self, app):
        """Multi-dog household: dog A has AM+PM, dog B has only AM.
        Discount fires once (for dog A only)."""
        with app.app_context():
            u, dog_a = make_client_with_dog('inv_multidog_one_double@test.com')
            dog_b = Dog(name='SecondDog', breed='Mutt')
            db.session.add(dog_b)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog_b.id, user_id=u.id, role='primary'))
            st = make_walk_service()
            make_pricing_config()
            add_booking(u, dog_a, st, MON_1, 'Morning',   status='confirmed')
            add_booking(u, dog_a, st, MON_1, 'Afternoon', status='confirmed')
            add_booking(u, dog_b, st, MON_1, 'Morning',   status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 3
            assert inv['doubles'] == 1
            assert inv['subtotal'] == round(WALK_PRICE * 3 - DOUBLE_DISCOUNT, 2)

    def test_double_slot_discount_per_dog_when_both_dogs_have_both_slots(self, app):
        """Multi-dog household: dog A and dog B both have AM+PM on the same day.
        Discount fires twice — once per dog."""
        with app.app_context():
            u, dog_a = make_client_with_dog('inv_multidog_both_double@test.com')
            dog_b = Dog(name='SecondDog', breed='Mutt')
            db.session.add(dog_b)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog_b.id, user_id=u.id, role='primary'))
            st = make_walk_service()
            make_pricing_config()
            add_booking(u, dog_a, st, MON_1, 'Morning',   status='confirmed')
            add_booking(u, dog_a, st, MON_1, 'Afternoon', status='confirmed')
            add_booking(u, dog_b, st, MON_1, 'Morning',   status='confirmed')
            add_booking(u, dog_b, st, MON_1, 'Afternoon', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 4
            assert inv['doubles'] == 2
            assert inv['subtotal'] == round(WALK_PRICE * 4 - DOUBLE_DISCOUNT * 2, 2)

    def test_late_cancel_is_billable(self, app):
        """Cancel with < 5 days notice → charged."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_lc@test.com')
            st = make_walk_service()
            make_pricing_config()
            # Walk on MON_1; cancelled 2 days before (5 - 2 = 3 < 5)
            cancelled_at = datetime.datetime.combine(
                MON_1 - datetime.timedelta(days=2), datetime.time.min
            )
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=cancelled_at)
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 1
            assert inv['subtotal'] == WALK_PRICE

    def test_early_cancel_not_billable(self, app):
        """Cancel with >= 5 days notice → not charged."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_ec@test.com')
            st = make_walk_service()
            make_pricing_config()
            # Walk on MON_1; cancelled 7 days before (>= 5 days notice)
            cancelled_at = datetime.datetime.combine(
                MON_1 - datetime.timedelta(days=7), datetime.time.min
            )
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=cancelled_at)
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 0
            assert inv['subtotal'] == 0.0

    def test_cancel_without_cancelled_at_not_billable(self, app):
        """Cancelled booking with no cancelled_at timestamp → not charged."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_nc@test.com')
            st = make_walk_service()
            make_pricing_config()
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=None)
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 0
            assert inv['subtotal'] == 0.0

    def test_admin_cancelled_not_billable_even_with_short_notice(self, app):
        """Admin cancellations (incl. closures) never bill, even inside the notice window."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_admin_cancel@test.com')
            st = make_walk_service()
            make_pricing_config()
            cancelled_at = datetime.datetime.combine(
                MON_1 - datetime.timedelta(days=2), datetime.time.min
            )
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=cancelled_at,
                        cancelled_by='admin')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 0
            assert inv['subtotal'] == 0.0

    def test_client_cancelled_short_notice_still_billable(self, app):
        """Explicit client cancel within notice window → still billable (regression)."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_client_cancel@test.com')
            st = make_walk_service()
            make_pricing_config()
            cancelled_at = datetime.datetime.combine(
                MON_1 - datetime.timedelta(days=2), datetime.time.min
            )
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=cancelled_at,
                        cancelled_by='client')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 1
            assert inv['subtotal'] == WALK_PRICE

    def test_admin_cancel_with_bill_flag_is_billable(self, app):
        """Admin cancel where the admin chose to bill (bill_cancellation=True) → charged."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_admin_bill@test.com')
            st = make_walk_service()
            make_pricing_config()
            cancelled_at = datetime.datetime.combine(
                MON_1 - datetime.timedelta(days=2), datetime.time.min
            )
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=cancelled_at,
                        cancelled_by='admin', bill_cancellation=True)
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 1
            assert inv['subtotal'] == WALK_PRICE

    def test_admin_cancel_waived_not_billable(self, app):
        """Admin cancel waived (bill_cancellation=False) → not charged, even inside window."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_admin_waive@test.com')
            st = make_walk_service()
            make_pricing_config()
            cancelled_at = datetime.datetime.combine(
                MON_1 - datetime.timedelta(days=2), datetime.time.min
            )
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=cancelled_at,
                        cancelled_by='admin', bill_cancellation=False)
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 0
            assert inv['subtotal'] == 0.0

    def test_client_cancel_waived_overrides_default(self, app):
        """An explicit waive (False) beats the legacy client late-cancel default."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_client_waive@test.com')
            st = make_walk_service()
            make_pricing_config()
            cancelled_at = datetime.datetime.combine(
                MON_1 - datetime.timedelta(days=2), datetime.time.min
            )
            add_booking(u, dog, st, MON_1, 'Morning',
                        status='cancelled', cancelled_at=cancelled_at,
                        cancelled_by='client', bill_cancellation=False)
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_cancels'] == 0
            assert inv['subtotal'] == 0.0

    def test_multiple_walks_across_weeks(self, app):
        with app.app_context():
            u, dog = make_client_with_dog('inv_multi@test.com')
            st = make_walk_service()
            make_pricing_config()
            add_booking(u, dog, st, MON_1, 'Morning', status='confirmed')
            add_booking(u, dog, st, MON_2, 'Morning', status='confirmed')
            add_booking(u, dog, st, TUE_1, 'Afternoon', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 3
            assert inv['subtotal'] == round(WALK_PRICE * 3, 2)

    def test_bookings_outside_month_excluded(self, app):
        """Bookings before month_start or on/after month_end are not included."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_outside@test.com')
            st = make_walk_service()
            make_pricing_config()
            # One day before the month
            add_booking(u, dog, st, datetime.date(2026, 1, 31), 'Morning', status='confirmed')
            # One day after the month (month_end is exclusive)
            add_booking(u, dog, st, datetime.date(2026, 3, 1),  'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 0
            assert inv['subtotal'] == 0.0

    def test_no_pricing_config_zero_subtotal(self, app):
        """If no PricingConfig covers the booking date, subtotal stays 0."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_noconfig@test.com')
            st = make_walk_service()
            # No PricingConfig added — config_for() returns None for all dates
            add_booking(u, dog, st, MON_1, 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 1
            assert inv['subtotal'] == 0.0

    # ── Weekly discount tests ────────────────────────────────────────────────

    def test_weekly_discount_applied_for_five_walks_in_one_week(self, app):
        """5 confirmed group walks in the same ISO week → weekly discount applied."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_wkly5@test.com')
            st = make_walk_service()
            make_pricing_config(weekly_discount=WEEKLY_DISCOUNT)
            # ISO week 6 of 2026: Mon 2 Feb – Fri 6 Feb
            for day in range(2, 7):  # Mon–Fri
                add_booking(u, dog, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 5
            assert inv['weekly_discount_weeks'] == 1
            expected = round(WALK_PRICE * 5 - WEEKLY_DISCOUNT * 5, 2)
            assert inv['subtotal'] == expected

    def test_weekly_discount_not_applied_for_four_walks(self, app):
        """4 confirmed group walks in a week → no weekly discount."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_wkly4@test.com')
            st = make_walk_service()
            make_pricing_config(weekly_discount=WEEKLY_DISCOUNT)
            for day in range(2, 6):  # Mon–Thu only (4 walks)
                add_booking(u, dog, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 4
            assert inv['weekly_discount_weeks'] == 0
            assert inv['weekly_discount_total'] == 0.0
            assert inv['subtotal'] == round(WALK_PRICE * 4, 2)

    def test_weekly_discount_drop_ins_not_counted(self, app):
        """Drop-ins do not count toward the 5-walk weekly threshold."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_wkly_di@test.com')
            walk_st = make_walk_service()
            di_st   = make_drop_in_service()
            make_pricing_config(weekly_discount=WEEKLY_DISCOUNT)
            # 3 walks + 2 drop-ins in the same week = 3 walks only → no discount
            for day in range(2, 5):  # Mon–Wed walks
                add_booking(u, dog, walk_st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            for day in range(5, 7):  # Thu–Fri drop-ins
                add_booking(u, dog, di_st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 3
            assert inv['total_drop_ins'] == 2
            assert inv['weekly_discount_weeks'] == 0

    def test_weekly_discount_cumulative_with_double_slot(self, app):
        """Weekly discount and double-slot discount both apply independently."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_wkly_dbl@test.com')
            st = make_walk_service()
            make_pricing_config(
                double_slot_discount=DOUBLE_DISCOUNT,
                weekly_discount=WEEKLY_DISCOUNT,
            )
            # Mon–Fri morning walks (5) + Mon AM+PM double slot
            for day in range(2, 7):  # Mon–Fri mornings
                add_booking(u, dog, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            # Monday afternoon as well → double-slot day, 6 walks total in the week
            add_booking(u, dog, st, datetime.date(2026, 2, 2), 'Afternoon', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['total_walks'] == 6
            assert inv['doubles'] == 1
            assert inv['weekly_discount_weeks'] == 1
            expected = round(
                WALK_PRICE * 6
                - DOUBLE_DISCOUNT          # double-slot discount (1 day)
                - WEEKLY_DISCOUNT * 6,     # weekly discount (6 walks)
                2
            )
            assert inv['subtotal'] == expected

    def test_weekly_discount_zero_when_config_discount_is_zero(self, app):
        """No weekly discount applied when weekly_discount is 0 in config."""
        with app.app_context():
            u, dog = make_client_with_dog('inv_wkly_zero@test.com')
            st = make_walk_service()
            make_pricing_config(weekly_discount=0.00)  # no weekly discount configured
            for day in range(2, 7):
                add_booking(u, dog, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            db.session.commit()
            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert inv['weekly_discount_weeks'] == 0
            assert inv['weekly_discount_total'] == 0.0
            assert inv['subtotal'] == round(WALK_PRICE * 5, 2)


# ---------------------------------------------------------------------------
# Revenue dashboard — weekly discount must be netted off (matches invoices)
# ---------------------------------------------------------------------------

from app.blueprints.admin.views.revenue import _revenue_for_range

MONTH_END_INCL = datetime.date(2026, 2, 28)   # inclusive end, as the route passes


class TestRevenueWeeklyDiscount:
    def test_revenue_nets_weekly_discount_and_matches_invoice(self, app):
        """5 walks in one ISO week → revenue total nets the weekly discount and
        equals the client's invoice subtotal (no more silent over-reporting)."""
        with app.app_context():
            u, dog = make_client_with_dog('rev_wkly@test.com')
            st = make_walk_service()
            make_pricing_config(weekly_discount=WEEKLY_DISCOUNT)
            for day in range(2, 7):  # Mon–Fri 2026-02-02..06, one ISO week
                add_booking(u, dog, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            db.session.commit()

            daily, weekly_discount = _revenue_for_range(MONTH_START, MONTH_END_INCL)
            gross = round(sum(r['revenue'] for r in daily), 2)
            net = round(gross - weekly_discount, 2)

            assert weekly_discount == round(WEEKLY_DISCOUNT * 5, 2)   # 5 walks
            assert gross == round(WALK_PRICE * 5, 2)

            inv = _invoice_for_client(u.id, MONTH_START, MONTH_END, all_configs())
            assert net == inv['subtotal']

    def test_revenue_no_discount_below_threshold(self, app):
        """4 walks in a week → no weekly discount netted."""
        with app.app_context():
            u, dog = make_client_with_dog('rev_nowkly@test.com')
            st = make_walk_service()
            make_pricing_config(weekly_discount=WEEKLY_DISCOUNT)
            for day in range(2, 6):  # only 4 walks
                add_booking(u, dog, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            db.session.commit()

            daily, weekly_discount = _revenue_for_range(MONTH_START, MONTH_END_INCL)
            assert weekly_discount == 0.0

    def test_weekly_discount_grouped_per_household_not_globally(self, app):
        """Two clients with 3 walks each (6 total) do NOT trigger the discount —
        the ≥5 threshold is per household, not across the whole business."""
        with app.app_context():
            st = make_walk_service()
            make_pricing_config(weekly_discount=WEEKLY_DISCOUNT)
            u1, dog1 = make_client_with_dog('rev_hh1@test.com')
            u2, dog2 = make_client_with_dog('rev_hh2@test.com')
            for day in range(2, 5):  # 3 walks each
                add_booking(u1, dog1, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
                add_booking(u2, dog2, st, datetime.date(2026, 2, day), 'Morning', status='confirmed')
            db.session.commit()

            _daily, weekly_discount = _revenue_for_range(MONTH_START, MONTH_END_INCL)
            assert weekly_discount == 0.0


# ---------------------------------------------------------------------------
# /admin/invoicing list route — membership test must be presence of a Client
# record, NOT role == 'client', so dual-role users (walker + client) appear.
# ---------------------------------------------------------------------------

class TestInvoicingListDualRole:
    def _make_admin(self):
        admin = User(
            firstname='Admin', lastname='User', email='inv_admin@test.com',
            role='walker', is_admin=True,
            hashed_password=generate_password_hash('Testpass1!'), active=True,
        )
        db.session.add(admin)
        db.session.flush()
        return admin

    def test_dual_role_user_appears_in_invoicing_list(self, app, client):
        """A user with role='walker' but a Client record + billable booking is a
        billable client and must show in /admin/invoicing. Regression: the list
        previously filtered role == 'client' and silently dropped them, even
        though their /invoicing/<id> detail page (User.client != None) worked."""
        with app.app_context():
            self._make_admin()

            # Dual-role user: role='walker', yet owns a dog and has a billable walk
            dual = User(
                firstname='Dual', lastname='RoleClient', email='inv_dual@test.com',
                role='walker', is_admin=False,
                hashed_password=generate_password_hash('Testpass1!'), active=True,
            )
            db.session.add(dual)
            db.session.flush()
            db.session.add(Client(user_id=dual.id, onboarding_completed=True))
            dog = Dog(name='DualDog', breed='Mutt')
            db.session.add(dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=dual.id, role='primary'))
            st = make_walk_service()
            make_pricing_config()
            add_booking(dual, dog, st, MON_1, 'Morning', status='confirmed')
            db.session.commit()

        client.post('/auth/login',
                    data={'email': 'inv_admin@test.com', 'password': 'Testpass1!'},
                    follow_redirects=True)
        resp = client.get('/admin/invoicing?month=2026-02')
        assert resp.status_code == 200
        assert b'RoleClient' in resp.data

    def test_dual_role_primary_is_billed_for_co_owners_booking(self, app):
        """Billing follows the *primary* owner. When a co-owned dog's primary is a
        dual-role user (walker + client), the primary's invoice must include a
        booking made by the secondary co-owner — and the co-owner (secondary,
        no primary dog) is NOT billed for it. Before the list fix this household's
        entire bill silently vanished: primary hidden, secondary never billable."""
        with app.app_context():
            # Primary = dual-role user (role='walker' + Client record + dog)
            primary = User(
                firstname='Dual', lastname='Primary', email='inv_dual_primary@test.com',
                role='walker', is_admin=False,
                hashed_password=generate_password_hash('Testpass1!'), active=True,
            )
            db.session.add(primary)
            db.session.flush()
            db.session.add(Client(user_id=primary.id, onboarding_completed=True))
            dog = Dog(name='SharedDog', breed='Mutt')
            db.session.add(dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=primary.id, role='primary'))

            # Co-owner = plain client, secondary on the same dog
            co = make_user('inv_coowner@test.com')   # role='client'
            db.session.add(Client(user_id=co.id, onboarding_completed=True))
            db.session.add(DogOwner(dog_id=dog.id, user_id=co.id, role='secondary'))

            st = make_walk_service()
            make_pricing_config()
            # Booking on the shared dog, MADE BY the co-owner (user_id = co)
            add_booking(co, dog, st, MON_1, 'Morning', status='confirmed')
            db.session.commit()

            # Primary (dual-role) absorbs the household bill for the co-owner's walk
            inv_primary = _invoice_for_client(primary.id, MONTH_START, MONTH_END, all_configs())
            assert inv_primary is not None
            assert inv_primary['total_billable'] == 1
            assert inv_primary['subtotal'] == WALK_PRICE

            # Co-owner has no primary dog → not billed for the shared dog (no double-bill)
            inv_co = _invoice_for_client(co.id, MONTH_START, MONTH_END, all_configs())
            assert inv_co is None


# ---------------------------------------------------------------------------
# join_dog_access — a dual-role user (walker + Client record) can be added as a
# co-owner. Membership test is presence of a Client record, not role == 'client'.
# ---------------------------------------------------------------------------

class TestCoOwnerDualRole:
    def test_dual_role_user_can_be_added_as_co_owner(self, app, client):
        """Granting shared dog access to a dual-role user (role='walker' with a
        Client record) must succeed. Regression: join_dog_access previously
        filtered role == 'client' and rejected them with 'Secondary client not
        found' even though they own dogs and use the client view."""
        with app.app_context():
            admin = User(
                firstname='Admin', lastname='User', email='join_admin@test.com',
                role='walker', is_admin=True,
                hashed_password=generate_password_hash('Testpass1!'), active=True,
            )
            db.session.add(admin)

            # Primary = plain client with a dog
            primary, dog = make_client_with_dog('join_primary@test.com')

            # Prospective co-owner = dual-role user (role='walker' + Client record)
            dual = User(
                firstname='Dual', lastname='CoOwner', email='join_dual@test.com',
                role='walker', is_admin=False,
                hashed_password=generate_password_hash('Testpass1!'), active=True,
            )
            db.session.add(dual)
            db.session.flush()
            db.session.add(Client(user_id=dual.id, onboarding_completed=True))
            db.session.commit()
            primary_id, dog_id, dual_id = primary.id, dog.id, dual.id

        client.post('/auth/login',
                    data={'email': 'join_admin@test.com', 'password': 'Testpass1!'},
                    follow_redirects=True)
        resp = client.post(
            f'/admin/clients/{primary_id}/join',
            json={'dog_id': dog_id, 'secondary_user_id': dual_id},
        )
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

        with app.app_context():
            link = DogOwner.query.filter_by(
                dog_id=dog_id, user_id=dual_id, role='secondary').first()
            assert link is not None
