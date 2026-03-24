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
from app.blueprints.admin.routes import _invoice_for_client


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
):
    cfg = PricingConfig(
        effective_from=effective_from,
        price_per_walk=price_per_walk,
        price_per_drop_in=price_per_drop_in,
        double_slot_discount=double_slot_discount,
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


def add_booking(user, dog, service, date, slot, status='confirmed', cancelled_at=None):
    b = Booking(
        user_id=user.id, dog_id=dog.id,
        service_type_id=service.id,
        date=date, slot=slot, status=status,
        cancelled_at=cancelled_at,
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
