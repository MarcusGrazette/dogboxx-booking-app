"""
Drop-in service tests.

Covers:
- get_drop_in_capacity(): no walkers, does_drop_ins flag, unavailability, booked count
- check_availability() with drop-in service type: available / full / no walkers
- POST /book_drop_in (AJAX endpoint): happy path, waitlisted, duplicate, no service,
  no walkers, past date, invalid slot, admin notification
"""
import datetime
import json
import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import (
    User, Client, Dog, DogOwner, Walker, WalkerSchedule,
    WalkerUnavailability, ServiceType, Booking, Notification,
)
from app.capacity import get_drop_in_capacity, check_availability


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONDAY = datetime.date(2026, 3, 9)   # known Monday


# ---------------------------------------------------------------------------
# Truncation fixture for integration tests
# ---------------------------------------------------------------------------

TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'walker_schedules', 'walker_unavailabilities',
    'walkers', 'clients', 'service_types', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='client', is_admin=False):
    u = User(
        firstname='Test', lastname='User',
        email=email, role=role, is_admin=is_admin,
        hashed_password=generate_password_hash('Testpass1!'),
        active=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def make_drop_in_walker(email, day_of_week=None, slot='Morning', does_drop_ins=True):
    """Create a walker with does_drop_ins flag and optional schedule."""
    u = make_user(email, role='walker')
    w = Walker(user_id=u.id, does_drop_ins=does_drop_ins)
    db.session.add(w)
    db.session.flush()
    if day_of_week is not None:
        s = WalkerSchedule(walker_id=w.id, day_of_week=day_of_week, slot=slot, active=True)
        db.session.add(s)
        db.session.flush()
    return w


def make_drop_in_service(capacity=6):
    st = ServiceType(
        name='Drop In',
        slug='drop-in',
        capacity_model='walker_assigned',
        slot_type='morning_afternoon',
        requires_walker=True,
        default_max_capacity=capacity,
        active=True,
        settings={},
    )
    db.session.add(st)
    db.session.flush()
    return st


def make_group_walk_service(capacity=6):
    st = ServiceType(
        name='Group Walk',
        slug='group-walk',
        capacity_model='walker_assigned',
        slot_type='morning_afternoon',
        requires_walker=True,
        default_max_capacity=capacity,
        active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


def make_client_with_dog(email):
    u = make_user(email, role='client')
    db.session.add(Client(user_id=u.id, onboarding_completed=True))
    dog = Dog(name='TestDog', breed='Mutt')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=u.id, role='primary'))
    db.session.flush()
    return u, dog


def login(flask_client, email, password='Testpass1!'):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': password,
    }, follow_redirects=True)


def tomorrow():
    return datetime.date.today() + datetime.timedelta(days=1)


def post_book_drop_in(flask_client, date, slot):
    return flask_client.post(
        '/book_drop_in',
        data=json.dumps({'date': date, 'slot': slot}),
        content_type='application/json',
    )


# ---------------------------------------------------------------------------
# Unit tests — get_drop_in_capacity()
# ---------------------------------------------------------------------------

class TestGetDropInCapacity:

    def test_no_walkers_returns_zero(self, app):
        with app.app_context():
            make_drop_in_service(capacity=6)
            total, booked, available = get_drop_in_capacity(MONDAY, 'Morning')
            assert total == 0
            assert booked == 0
            assert available == 0

    def test_regular_walker_excluded_from_drop_in_capacity(self, app):
        """A walker with does_drop_ins=False is not counted for drop-in capacity."""
        with app.app_context():
            make_drop_in_service(capacity=6)
            # Regular walker, not drop-in eligible
            make_drop_in_walker(
                'regular_walker@test.com',
                day_of_week=MONDAY.weekday(),
                slot='Morning',
                does_drop_ins=False,
            )
            total, booked, available = get_drop_in_capacity(MONDAY, 'Morning')
            assert total == 0

    def test_drop_in_walker_counted(self, app):
        with app.app_context():
            make_drop_in_service(capacity=4)
            make_drop_in_walker(
                'di_walker@test.com',
                day_of_week=MONDAY.weekday(),
                slot='Morning',
                does_drop_ins=True,
            )
            total, booked, available = get_drop_in_capacity(MONDAY, 'Morning')
            assert total == 4
            assert available == 4

    def test_two_drop_in_walkers_double_capacity(self, app):
        with app.app_context():
            make_drop_in_service(capacity=3)
            make_drop_in_walker('di_w1@test.com', day_of_week=MONDAY.weekday(), slot='Morning')
            make_drop_in_walker('di_w2@test.com', day_of_week=MONDAY.weekday(), slot='Morning')
            total, booked, available = get_drop_in_capacity(MONDAY, 'Morning')
            assert total == 6
            assert available == 6

    def test_booked_drop_ins_reduce_available(self, app):
        with app.app_context():
            st = make_drop_in_service(capacity=6)
            make_drop_in_walker('di_w_b@test.com', day_of_week=MONDAY.weekday(), slot='Morning')
            client_u, dog = make_client_with_dog('client_b@test.com')
            db.session.add(Booking(
                user_id=client_u.id, dog_id=dog.id,
                service_type_id=st.id, date=MONDAY, slot='Morning',
                status='confirmed',
            ))
            db.session.flush()
            total, booked, available = get_drop_in_capacity(MONDAY, 'Morning')
            assert booked == 1
            assert available == 5

    def test_cancelled_drop_ins_not_counted(self, app):
        with app.app_context():
            st = make_drop_in_service(capacity=6)
            make_drop_in_walker('di_w_c@test.com', day_of_week=MONDAY.weekday(), slot='Morning')
            client_u, dog = make_client_with_dog('client_c@test.com')
            db.session.add(Booking(
                user_id=client_u.id, dog_id=dog.id,
                service_type_id=st.id, date=MONDAY, slot='Morning',
                status='cancelled',
            ))
            db.session.flush()
            total, booked, available = get_drop_in_capacity(MONDAY, 'Morning')
            assert booked == 0
            assert available == 6

    def test_unavailability_excludes_drop_in_walker(self, app):
        with app.app_context():
            make_drop_in_service(capacity=6)
            w = make_drop_in_walker('di_w_u@test.com', day_of_week=MONDAY.weekday(), slot='Morning')
            db.session.add(WalkerUnavailability(walker_id=w.id, date=MONDAY, slot='Morning'))
            db.session.flush()
            total, booked, available = get_drop_in_capacity(MONDAY, 'Morning')
            assert total == 0


# ---------------------------------------------------------------------------
# Unit tests — check_availability() with drop-in service
# ---------------------------------------------------------------------------

class TestCheckAvailabilityDropIn:

    def test_no_drop_in_walkers_returns_unavailable(self, app):
        with app.app_context():
            st = make_drop_in_service(capacity=6)
            available, can_waitlist, msg = check_availability(st, MONDAY, slot='Morning')
            assert available is False
            assert can_waitlist is False

    def test_drop_in_available(self, app):
        with app.app_context():
            st = make_drop_in_service(capacity=6)
            make_drop_in_walker('di_avail@test.com', day_of_week=MONDAY.weekday(), slot='Morning')
            available, can_waitlist, msg = check_availability(st, MONDAY, slot='Morning')
            assert available is True

    def test_full_drop_in_slot_returns_waitlist(self, app):
        with app.app_context():
            st = make_drop_in_service(capacity=1)
            w = make_drop_in_walker('di_full@test.com', day_of_week=MONDAY.weekday(), slot='Morning')
            client_u, dog = make_client_with_dog('client_full@test.com')
            db.session.add(Booking(
                user_id=client_u.id, dog_id=dog.id,
                service_type_id=st.id, date=MONDAY, slot='Morning',
                status='confirmed',
            ))
            db.session.flush()
            available, can_waitlist, msg = check_availability(st, MONDAY, slot='Morning')
            assert available is False
            assert can_waitlist is True

    def test_slot_required_for_drop_in(self, app):
        with app.app_context():
            st = make_drop_in_service()
            available, can_waitlist, msg = check_availability(st, MONDAY, slot=None)
            assert available is False
            assert 'Slot is required' in msg


# ---------------------------------------------------------------------------
# Integration tests — POST /book_drop_in
# ---------------------------------------------------------------------------

class TestBookDropIn:

    def test_happy_path_creates_requested_booking(self, app, client):
        with app.app_context():
            tom = tomorrow()
            make_drop_in_service(capacity=6)
            make_drop_in_walker(
                'di_hp_w@test.com',
                day_of_week=tom.weekday(), slot='Morning',
            )
            user, dog = make_client_with_dog('di_hp_cl@test.com')
            db.session.commit()

        login(client, 'di_hp_cl@test.com')
        resp = post_book_drop_in(client, tomorrow().isoformat(), 'Morning')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['status'] == 'requested'
        assert data['booking']['is_drop_in'] is True

        with app.app_context():
            b = Booking.query.filter_by(status='requested').first()
            assert b is not None
            assert b.service_type.slug == 'drop-in'

    def test_full_slot_creates_waitlisted_booking(self, app, client):
        with app.app_context():
            tom = tomorrow()
            st = make_drop_in_service(capacity=1)
            make_drop_in_walker('di_wl_w@test.com', day_of_week=tom.weekday(), slot='Morning')
            # Fill the slot
            filler_u, filler_dog = make_client_with_dog('di_filler@test.com')
            db.session.add(Booking(
                user_id=filler_u.id, dog_id=filler_dog.id,
                service_type_id=st.id, date=tom, slot='Morning',
                status='confirmed',
            ))
            user, dog = make_client_with_dog('di_wl_cl@test.com')
            db.session.commit()

        login(client, 'di_wl_cl@test.com')
        resp = post_book_drop_in(client, tomorrow().isoformat(), 'Morning')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['status'] == 'waitlisted'

    def test_duplicate_booking_rejected(self, app, client):
        with app.app_context():
            tom = tomorrow()
            st = make_drop_in_service(capacity=6)
            make_drop_in_walker('di_dup_w@test.com', day_of_week=tom.weekday(), slot='Morning')
            user, dog = make_client_with_dog('di_dup_cl@test.com')
            db.session.add(Booking(
                user_id=user.id, dog_id=dog.id,
                service_type_id=st.id, date=tom, slot='Morning',
                status='confirmed',
            ))
            db.session.commit()

        login(client, 'di_dup_cl@test.com')
        resp = post_book_drop_in(client, tomorrow().isoformat(), 'Morning')

        assert resp.status_code == 409
        data = resp.get_json()
        assert data['success'] is False
        assert 'already booked' in data['message']

    def test_no_drop_in_service_returns_503(self, app, client):
        """If no active drop-in ServiceType exists, return 503."""
        with app.app_context():
            # Only create a group-walk service — no drop-in
            make_group_walk_service()
            user, dog = make_client_with_dog('di_noservice@test.com')
            db.session.commit()

        login(client, 'di_noservice@test.com')
        resp = post_book_drop_in(client, tomorrow().isoformat(), 'Morning')

        assert resp.status_code == 503

    def test_no_drop_in_walkers_returns_409(self, app, client):
        """Drop-in service exists but no eligible walkers → 409."""
        with app.app_context():
            tom = tomorrow()
            make_drop_in_service(capacity=6)
            # Walker is NOT drop-in eligible
            make_drop_in_walker(
                'di_nowalk_w@test.com',
                day_of_week=tom.weekday(), slot='Morning',
                does_drop_ins=False,
            )
            user, dog = make_client_with_dog('di_nowalk_cl@test.com')
            db.session.commit()

        login(client, 'di_nowalk_cl@test.com')
        resp = post_book_drop_in(client, tomorrow().isoformat(), 'Morning')

        assert resp.status_code == 409
        data = resp.get_json()
        assert data['success'] is False

    def test_past_date_rejected(self, app, client):
        with app.app_context():
            make_drop_in_service()
            user, dog = make_client_with_dog('di_past@test.com')
            db.session.commit()

        login(client, 'di_past@test.com')
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        resp = post_book_drop_in(client, yesterday, 'Morning')

        assert resp.status_code == 400

    def test_invalid_slot_rejected(self, app, client):
        with app.app_context():
            make_drop_in_service()
            user, dog = make_client_with_dog('di_slot@test.com')
            db.session.commit()

        login(client, 'di_slot@test.com')
        resp = post_book_drop_in(client, tomorrow().isoformat(), 'Lunchtime')

        assert resp.status_code == 400

    def test_unauthenticated_user_redirected(self, app, client):
        resp = post_book_drop_in(client, tomorrow().isoformat(), 'Morning')
        # Unauthenticated → redirect to login
        assert resp.status_code in (302, 401)

    def test_drop_in_request_notifies_admins(self, app, client):
        with app.app_context():
            tom = tomorrow()
            make_drop_in_service(capacity=6)
            make_drop_in_walker('di_notif_w@test.com', day_of_week=tom.weekday(), slot='Morning')
            admin = make_user('di_admin@test.com', role='walker', is_admin=True)
            user, dog = make_client_with_dog('di_notif_cl@test.com')
            db.session.commit()
            admin_id = admin.id

        login(client, 'di_notif_cl@test.com')
        post_book_drop_in(client, tomorrow().isoformat(), 'Morning')

        with app.app_context():
            notifs = Notification.query.filter_by(
                recipient_id=admin_id,
                notification_type='booking_requested',
            ).all()
            assert len(notifs) >= 1
