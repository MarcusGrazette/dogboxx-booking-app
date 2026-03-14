"""
T2 — Capacity and availability unit tests.

Tests cover:
- get_available_walkers: 0, partial, full walker sets + unavailability exclusions
- get_max_per_walker: reads from ServiceType, falls back to 6
- get_walk_capacity: total/booked/available arithmetic
- check_availability: available / waitlisted / no-walkers branches
- get_slot_availability_summary: both slots summarised
"""
import datetime
import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    User, Walker, WalkerSchedule, WalkerUnavailability,
    ServiceType, Booking, Client, Dog, DogOwner,
)
from app.capacity import (
    get_available_walkers,
    get_max_per_walker,
    get_walk_capacity,
    check_availability,
    get_slot_availability_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MONDAY = datetime.date(2026, 3, 9)   # known Monday
TUESDAY = datetime.date(2026, 3, 10)


def make_walker_user(email):
    user = User(
        firstname='Walker', lastname='Test',
        email=email, role='walker',
        hashed_password=generate_password_hash('pass'),
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    walker = Walker(user_id=user.id)
    db.session.add(walker)
    db.session.flush()
    return walker


def schedule_walker(walker, day_of_week, slot, active=True):
    """Give a walker a default schedule entry."""
    s = WalkerSchedule(
        walker_id=walker.id,
        day_of_week=day_of_week,
        slot=slot,
        active=active,
    )
    db.session.add(s)
    db.session.flush()
    return s


def make_group_walk_service(capacity=6):
    st = ServiceType(
        name='Group Walk', slug='group-walk',
        capacity_model='walker_assigned',
        slot_type='morning_afternoon',
        requires_walker=True,
        default_max_capacity=capacity,
        active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


_client_counter = 0


def make_client_user():
    global _client_counter
    _client_counter += 1
    user = User(
        firstname='Client', lastname=f'Test{_client_counter}',
        email=f'client_{_client_counter}@testcapacity.com',
        role='client',
        hashed_password=generate_password_hash('pass'),
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    client = Client(user_id=user.id, onboarding_completed=True)
    db.session.add(client)
    db.session.flush()
    return user


_booking_counter = 0


def make_booking(user, service_type, date, slot, status='confirmed', walker=None):
    """Create a booking with a fresh dog each time (avoids SQLite unique constraint on dog+date+slot)."""
    global _booking_counter
    _booking_counter += 1
    dog = Dog(name=f'Dog{_booking_counter}', breed='Mutt')
    db.session.add(dog)
    db.session.flush()
    assoc = DogOwner(dog_id=dog.id, user_id=user.id, role='primary')
    db.session.add(assoc)
    db.session.flush()
    b = Booking(
        user_id=user.id,
        dog_id=dog.id,
        service_type_id=service_type.id,
        date=date,
        slot=slot,
        status=status,
        walker_id=walker.id if walker else None,
    )
    db.session.add(b)
    db.session.flush()
    return b


# ---------------------------------------------------------------------------
# get_available_walkers
# ---------------------------------------------------------------------------

class TestGetAvailableWalkers:

    def test_no_walkers_scheduled_returns_empty(self, app):
        with app.app_context():
            result = get_available_walkers(MONDAY, 'Morning')
            assert result == []

    def test_one_walker_scheduled_returns_them(self, app):
        with app.app_context():
            walker = make_walker_user('walker_one@test.com')
            schedule_walker(walker, MONDAY.weekday(), 'Morning')  # Monday = 0
            result = get_available_walkers(MONDAY, 'Morning')
            assert len(result) == 1
            assert result[0].id == walker.id

    def test_walker_wrong_slot_excluded(self, app):
        with app.app_context():
            walker = make_walker_user('walker_slot@test.com')
            schedule_walker(walker, MONDAY.weekday(), 'Afternoon')
            result = get_available_walkers(MONDAY, 'Morning')
            assert len(result) == 0

    def test_walker_wrong_day_excluded(self, app):
        with app.app_context():
            walker = make_walker_user('walker_day@test.com')
            schedule_walker(walker, TUESDAY.weekday(), 'Morning')  # Tuesday
            result = get_available_walkers(MONDAY, 'Morning')
            assert len(result) == 0

    def test_unavailability_excludes_walker(self, app):
        with app.app_context():
            walker = make_walker_user('walker_unavail@test.com')
            schedule_walker(walker, MONDAY.weekday(), 'Morning')
            unavail = WalkerUnavailability(
                walker_id=walker.id, date=MONDAY, slot='Morning'
            )
            db.session.add(unavail)
            db.session.flush()
            result = get_available_walkers(MONDAY, 'Morning')
            assert len(result) == 0

    def test_unavailability_different_slot_does_not_exclude(self, app):
        with app.app_context():
            walker = make_walker_user('walker_unavail2@test.com')
            schedule_walker(walker, MONDAY.weekday(), 'Morning')
            unavail = WalkerUnavailability(
                walker_id=walker.id, date=MONDAY, slot='Afternoon'
            )
            db.session.add(unavail)
            db.session.flush()
            result = get_available_walkers(MONDAY, 'Morning')
            assert len(result) == 1

    def test_inactive_walker_excluded(self, app):
        with app.app_context():
            walker = make_walker_user('walker_inactive@test.com')
            walker.user.active = False
            db.session.flush()
            schedule_walker(walker, MONDAY.weekday(), 'Morning')
            result = get_available_walkers(MONDAY, 'Morning')
            assert len(result) == 0

    def test_multiple_walkers_partial_unavailability(self, app):
        with app.app_context():
            w1 = make_walker_user('walker_m1@test.com')
            w2 = make_walker_user('walker_m2@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            schedule_walker(w2, MONDAY.weekday(), 'Morning')
            unavail = WalkerUnavailability(
                walker_id=w1.id, date=MONDAY, slot='Morning'
            )
            db.session.add(unavail)
            db.session.flush()
            result = get_available_walkers(MONDAY, 'Morning')
            assert len(result) == 1
            assert result[0].id == w2.id


# ---------------------------------------------------------------------------
# get_max_per_walker
# ---------------------------------------------------------------------------

class TestGetMaxPerWalker:

    def test_returns_fallback_when_no_service(self, app):
        with app.app_context():
            result = get_max_per_walker('group-walk')
            assert result == 6

    def test_reads_from_service_type(self, app):
        with app.app_context():
            make_group_walk_service(capacity=4)
            result = get_max_per_walker('group-walk')
            assert result == 4

    def test_returns_fallback_when_capacity_null(self, app):
        with app.app_context():
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned',
                slot_type='morning_afternoon',
                requires_walker=True,
                default_max_capacity=None,
                active=True,
            )
            db.session.add(st)
            db.session.flush()
            result = get_max_per_walker('group-walk')
            assert result == 6


# ---------------------------------------------------------------------------
# get_walk_capacity
# ---------------------------------------------------------------------------

class TestGetWalkCapacity:

    def test_no_walkers_returns_zero_total(self, app):
        with app.app_context():
            make_group_walk_service(capacity=6)
            total, booked, available = get_walk_capacity(MONDAY, 'Morning')
            assert total == 0
            assert booked == 0
            assert available == 0

    def test_two_walkers_correct_total(self, app):
        with app.app_context():
            make_group_walk_service(capacity=6)
            w1 = make_walker_user('cap_w1@test.com')
            w2 = make_walker_user('cap_w2@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            schedule_walker(w2, MONDAY.weekday(), 'Morning')
            total, booked, available = get_walk_capacity(MONDAY, 'Morning')
            assert total == 12
            assert booked == 0
            assert available == 12

    def test_booked_count_reduces_available(self, app):
        with app.app_context():
            st = make_group_walk_service(capacity=6)
            w1 = make_walker_user('cap_booked_w1@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            client_user = make_client_user()
            make_booking(client_user, st, MONDAY, 'Morning', status='confirmed')
            make_booking(client_user, st, MONDAY, 'Morning', status='requested')
            total, booked, available = get_walk_capacity(MONDAY, 'Morning')
            assert total == 6
            assert booked == 2
            assert available == 4

    def test_cancelled_bookings_not_counted(self, app):
        with app.app_context():
            st = make_group_walk_service(capacity=6)
            w1 = make_walker_user('cap_cancel_w1@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            client_user = make_client_user()
            make_booking(client_user, st, MONDAY, 'Morning', status='cancelled')
            make_booking(client_user, st, MONDAY, 'Morning', status='rejected')
            total, booked, available = get_walk_capacity(MONDAY, 'Morning')
            assert total == 6
            assert booked == 0
            assert available == 6

    def test_available_never_negative(self, app):
        with app.app_context():
            st = make_group_walk_service(capacity=2)
            w1 = make_walker_user('cap_neg_w1@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            client_user = make_client_user()
            # Book more than capacity (bypassing validation, testing floor)
            for _ in range(5):
                make_booking(client_user, st, MONDAY, 'Morning', status='confirmed')
            total, booked, available = get_walk_capacity(MONDAY, 'Morning')
            assert available == 0  # floored at 0


# ---------------------------------------------------------------------------
# check_availability
# ---------------------------------------------------------------------------

class TestCheckAvailability:

    def test_no_walkers_returns_unavailable(self, app):
        with app.app_context():
            st = make_group_walk_service()
            available, can_waitlist, msg = check_availability(st, MONDAY, slot='Morning')
            assert available is False
            assert can_waitlist is False
            assert 'No walkers' in msg

    def test_slots_available(self, app):
        with app.app_context():
            st = make_group_walk_service(capacity=6)
            w1 = make_walker_user('avail_w1@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            available, can_waitlist, msg = check_availability(st, MONDAY, slot='Morning')
            assert available is True
            assert can_waitlist is False

    def test_full_slot_returns_waitlist(self, app):
        with app.app_context():
            st = make_group_walk_service(capacity=1)
            w1 = make_walker_user('full_w1@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            client_user = make_client_user()
            make_booking(client_user, st, MONDAY, 'Morning', status='confirmed')
            available, can_waitlist, msg = check_availability(st, MONDAY, slot='Morning')
            assert available is False
            assert can_waitlist is True
            assert 'waitlist' in msg.lower()

    def test_slot_required_for_walk(self, app):
        with app.app_context():
            st = make_group_walk_service()
            available, can_waitlist, msg = check_availability(st, MONDAY, slot=None)
            assert available is False
            assert 'Slot is required' in msg


# ---------------------------------------------------------------------------
# get_slot_availability_summary
# ---------------------------------------------------------------------------

class TestGetSlotAvailabilitySummary:

    def test_returns_both_slots(self, app):
        with app.app_context():
            make_group_walk_service(capacity=6)
            result = get_slot_availability_summary(MONDAY)
            assert 'Morning' in result
            assert 'Afternoon' in result

    def test_summary_shape(self, app):
        with app.app_context():
            make_group_walk_service(capacity=6)
            w1 = make_walker_user('summary_w1@test.com')
            schedule_walker(w1, MONDAY.weekday(), 'Morning')
            result = get_slot_availability_summary(MONDAY)
            assert result['Morning']['total'] == 6
            assert result['Morning']['booked'] == 0
            assert result['Morning']['available'] == 6
            assert result['Afternoon']['total'] == 0
