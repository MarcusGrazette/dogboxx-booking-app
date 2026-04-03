"""
T3 — Booking creation tests.

Covers:
- Client one-off booking (POST /): happy path, waitlisted, duplicate rejected
- Client recurring booking (POST /recurring_booking): dates generated, weekends skipped, duplicates skipped
- Admin book_for_dog (POST /admin/book_for_dog): happy path, waitlisted, duplicate rejected
- Admin recurring_for_dog (POST /admin/recurring_for_dog): dates generated, weekends skipped, duplicates skipped
"""
import datetime
import json
import pytest
from werkzeug.security import generate_password_hash
from flask import url_for
from sqlalchemy import text

from app import db
from app.models import (
    User, Client, Dog, DogOwner, Walker, WalkerSchedule,
    ServiceType, Booking,
)


# ---------------------------------------------------------------------------
# Integration test isolation
#
# HTTP requests commit to the real DB — conftest's transaction rollback only
# covers direct db.session calls.  We truncate all relevant tables before each
# test so we always start from a clean slate.
# ---------------------------------------------------------------------------

TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'walker_schedules', 'walker_unavailabilities',
    'walkers', 'clients', 'service_types', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Truncate all tables before each test in this module."""
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield

# ---------------------------------------------------------------------------
# Date helpers — use tomorrow and next week to pass validation
# ---------------------------------------------------------------------------

def tomorrow():
    return datetime.date.today() + datetime.timedelta(days=1)


def next_monday():
    """Return the next Monday (skipping today if today is Monday)."""
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != 0:
        d += datetime.timedelta(days=1)
    return d


def next_tuesday():
    d = next_monday() + datetime.timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# DB helpers
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


def make_client_profile(user_id):
    c = Client(user_id=user_id, onboarding_completed=True)
    db.session.add(c)
    db.session.flush()
    return c


def make_dog(name='Buddy'):
    d = Dog(name=name, breed='Labrador')
    db.session.add(d)
    db.session.flush()
    return d


def attach_dog(dog_id, user_id):
    assoc = DogOwner(dog_id=dog_id, user_id=user_id, role='primary')
    db.session.add(assoc)
    db.session.flush()


def make_walker_with_schedule(email, day_of_week, slot):
    u = make_user(email, role='walker')
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    s = WalkerSchedule(walker_id=w.id, day_of_week=day_of_week, slot=slot, active=True)
    db.session.add(s)
    db.session.flush()
    return w


def make_service(capacity=6):
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


def login(flask_client, email, password='Testpass1!'):
    return flask_client.post('/auth/login', data={
        'email': email,
        'password': password,
    }, follow_redirects=True)


# ---------------------------------------------------------------------------
# T3a — Client one-off booking (POST /)
# ---------------------------------------------------------------------------

class TestClientOneOffBooking:

    def setup_method(self):
        """Create a fresh client + dog + service for each test."""
        self._email = f'client_oneoff_{id(self)}@test.com'

    def test_happy_path_status_requested(self, app, client):
        """Walker available → booking is auto-confirmed and assigned."""
        with app.app_context():
            # Create a walker scheduled for tomorrow's day-of-week
            tom = tomorrow()
            make_walker_with_schedule(
                f'walker_hp_{id(self)}@test.com',
                tom.weekday(), 'Morning'
            )
            make_service(capacity=6)
            user = make_user(self._email)
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()

        login(client, self._email)
        resp = client.post('/', data={
            'date': tomorrow().isoformat(),
            'slot': 'Morning',
        }, follow_redirects=True)

        assert resp.status_code == 200
        with app.app_context():
            # With a walker available, booking should be auto-confirmed
            booking = Booking.query.filter_by(status='confirmed').first()
            assert booking is not None
            assert booking.slot == 'Morning'
            assert booking.walker_id is not None

    def test_no_walkers_rejects_booking(self, app, client):
        """No walkers scheduled → no booking created."""
        with app.app_context():
            make_service(capacity=6)
            user = make_user(self._email)
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()

        login(client, self._email)
        client.post('/', data={
            'date': tomorrow().isoformat(),
            'slot': 'Morning',
        }, follow_redirects=True)

        with app.app_context():
            assert Booking.query.count() == 0

    def test_full_slot_creates_waitlisted_booking(self, app, client):
        """Slot at capacity → booking created with status=waitlisted."""
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule(
                f'walker_full_{id(self)}@test.com',
                tom.weekday(), 'Morning'
            )
            st = make_service(capacity=1)
            # Fill the slot
            filler_user = make_user(f'filler_{id(self)}@test.com')
            filler_dog = make_dog('Filler')
            attach_dog(filler_dog.id, filler_user.id)
            existing = Booking(
                user_id=filler_user.id, dog_id=filler_dog.id,
                service_type_id=st.id, date=tom, slot='Morning',
                status='confirmed',
            )
            db.session.add(existing)

            user = make_user(self._email)
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()

        login(client, self._email)
        client.post('/', data={
            'date': tomorrow().isoformat(),
            'slot': 'Morning',
        }, follow_redirects=True)

        with app.app_context():
            booking = Booking.query.filter_by(status='waitlisted').first()
            assert booking is not None

    def test_duplicate_booking_rejected(self, app, client):
        """Same dog + date + slot already booked → second attempt rejected."""
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule(
                f'walker_dup_{id(self)}@test.com',
                tom.weekday(), 'Morning'
            )
            st = make_service(capacity=6)
            user = make_user(self._email)
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            # Pre-existing active booking for the same dog/date/slot
            db.session.add(Booking(
                user_id=user.id, dog_id=dog.id,
                service_type_id=st.id, date=tom, slot='Morning',
                status='confirmed',
            ))
            db.session.commit()

        login(client, self._email)
        client.post('/', data={
            'date': tomorrow().isoformat(),
            'slot': 'Morning',
        }, follow_redirects=True)

        with app.app_context():
            # Still only 1 booking (the pre-existing one)
            assert Booking.query.count() == 1


# ---------------------------------------------------------------------------
# T3b — Client recurring booking (POST /recurring_booking)
# ---------------------------------------------------------------------------

class TestClientRecurringBooking:

    def _post(self, flask_client, payload):
        return flask_client.post(
            '/recurring_booking',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_daily_booking_skips_weekends(self, app, client):
        """Daily frequency: only weekdays created in a Mon–Sun span."""
        mon = next_monday()
        sun = mon + datetime.timedelta(days=6)   # includes Sat + Sun

        with app.app_context():
            # Schedule walker for every weekday so capacity exists Mon–Fri
            walker_email = f'walker_daily_{id(self)}@test.com'
            for dow in range(5):  # Mon=0 through Fri=4
                if dow == 0:
                    make_walker_with_schedule(walker_email, dow, 'Morning')
                else:
                    # Re-use the walker already created
                    from app.models import Walker as WModel
                    w = WModel.query.join(User).filter(User.email == walker_email).first()
                    s = WalkerSchedule(walker_id=w.id, day_of_week=dow, slot='Morning', active=True)
                    db.session.add(s)
                    db.session.flush()
            make_service(capacity=6)
            user = make_user(f'client_daily_{id(self)}@test.com')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()

        login(client, f'client_daily_{id(self)}@test.com')
        resp = self._post(client, {
            'start_date': mon.isoformat(),
            'end_date': sun.isoformat(),
            'slot': 'Morning',
            'frequency': 'daily',
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['success'] is True
        # 5 weekdays in Mon–Fri (Sat+Sun skipped)
        assert data['created'] + data['waitlisted'] == 5

    def test_weekly_booking_creates_correct_count(self, app, client):
        """Weekly frequency over 3 weeks creates 3 bookings."""
        mon = next_monday()
        end = mon + datetime.timedelta(weeks=2)   # Mon, Mon+7, Mon+14

        with app.app_context():
            make_walker_with_schedule(
                f'walker_weekly_{id(self)}@test.com',
                mon.weekday(), 'Morning'
            )
            make_service(capacity=6)
            user = make_user(f'client_weekly_{id(self)}@test.com')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()

        login(client, f'client_weekly_{id(self)}@test.com')
        resp = self._post(client, {
            'start_date': mon.isoformat(),
            'end_date': end.isoformat(),
            'slot': 'Morning',
            'frequency': 'weekly',
        })
        data = resp.get_json()
        assert data['success'] is True
        assert data['created'] + data['waitlisted'] == 3

    def test_duplicate_dates_skipped(self, app, client):
        """Dates where dog already has an active booking in that slot are skipped."""
        mon = next_monday()

        with app.app_context():
            make_walker_with_schedule(
                f'walker_dup_r_{id(self)}@test.com',
                mon.weekday(), 'Morning'
            )
            st = make_service(capacity=6)
            user = make_user(f'client_dup_r_{id(self)}@test.com')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            # Pre-book Monday
            db.session.add(Booking(
                user_id=user.id, dog_id=dog.id,
                service_type_id=st.id, date=mon, slot='Morning',
                status='confirmed',
            ))
            db.session.commit()

        login(client, f'client_dup_r_{id(self)}@test.com')
        resp = self._post(client, {
            'start_date': mon.isoformat(),
            'end_date': mon.isoformat(),
            'slot': 'Morning',
            'frequency': 'weekly',
        })
        data = resp.get_json()
        assert data['success'] is True
        assert data['skipped'] == 1
        assert data['created'] == 0

    def test_end_date_beyond_4_weeks_rejected(self, app, client):
        """Client cannot book beyond 4 weeks — should return error."""
        with app.app_context():
            user = make_user(f'client_4w_{id(self)}@test.com')
            make_client_profile(user.id)
            make_dog()
            db.session.commit()

        login(client, f'client_4w_{id(self)}@test.com')
        far_future = datetime.date.today() + datetime.timedelta(weeks=6)
        resp = self._post(client, {
            'start_date': tomorrow().isoformat(),
            'end_date': far_future.isoformat(),
            'slot': 'Morning',
            'frequency': 'daily',
        })
        data = resp.get_json()
        assert data['success'] is False
        assert 'within 4 weeks' in data['message']


# ---------------------------------------------------------------------------
# T3c — Admin book_for_dog (POST /admin/book_for_dog)
# ---------------------------------------------------------------------------

class TestAdminBookForDog:

    def _post(self, flask_client, payload):
        return flask_client.post(
            '/admin/book_for_dog',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_happy_path_creates_requested_booking(self, app, client):
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule(
                f'walker_abfd_{id(self)}@test.com',
                tom.weekday(), 'Morning'
            )
            make_service(capacity=6)
            admin = make_user(f'admin_abfd_{id(self)}@test.com', role='walker', is_admin=True)
            client_user = make_user(f'cl_abfd_{id(self)}@test.com', role='client')
            make_client_profile(client_user.id)
            dog = make_dog()
            attach_dog(dog.id, client_user.id)
            db.session.commit()
            dog_id = dog.id
            user_id = client_user.id
            admin_email = admin.email

        login(client, admin_email)
        resp = self._post(client, {
            'dog_id': dog_id,
            'user_id': user_id,
            'date': tomorrow().isoformat(),
            'slot': 'Morning',
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['success'] is True
        assert data['status'] == 'requested'

    def test_duplicate_rejected(self, app, client):
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule(
                f'walker_dup_a_{id(self)}@test.com',
                tom.weekday(), 'Morning'
            )
            st = make_service(capacity=6)
            admin = make_user(f'admin_dup_a_{id(self)}@test.com', role='walker', is_admin=True)
            client_user = make_user(f'cl_dup_a_{id(self)}@test.com', role='client')
            make_client_profile(client_user.id)
            dog = make_dog()
            attach_dog(dog.id, client_user.id)
            db.session.add(Booking(
                user_id=client_user.id, dog_id=dog.id,
                service_type_id=st.id, date=tom, slot='Morning',
                status='confirmed',
            ))
            db.session.commit()
            dog_id = dog.id
            user_id = client_user.id
            admin_email = admin.email

        login(client, admin_email)
        resp = self._post(client, {
            'dog_id': dog_id,
            'user_id': user_id,
            'date': tomorrow().isoformat(),
            'slot': 'Morning',
        })
        data = resp.get_json()
        assert resp.status_code == 400
        assert data['success'] is False
        assert 'already has a booking' in data['message']

    def test_full_slot_creates_waitlisted_booking(self, app, client):
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule(
                f'walker_wl_a_{id(self)}@test.com',
                tom.weekday(), 'Morning'
            )
            st = make_service(capacity=1)
            admin = make_user(f'admin_wl_a_{id(self)}@test.com', role='walker', is_admin=True)
            client_user = make_user(f'cl_wl_a_{id(self)}@test.com', role='client')
            make_client_profile(client_user.id)
            filler_user = make_user(f'filler_a_{id(self)}@test.com', role='client')
            filler_dog = make_dog('Filler')
            attach_dog(filler_dog.id, filler_user.id)
            db.session.add(Booking(
                user_id=filler_user.id, dog_id=filler_dog.id,
                service_type_id=st.id, date=tom, slot='Morning',
                status='confirmed',
            ))
            dog = make_dog()
            attach_dog(dog.id, client_user.id)
            db.session.commit()
            dog_id = dog.id
            user_id = client_user.id
            admin_email = admin.email

        login(client, admin_email)
        resp = self._post(client, {
            'dog_id': dog_id,
            'user_id': user_id,
            'date': tomorrow().isoformat(),
            'slot': 'Morning',
        })
        data = resp.get_json()
        assert data['success'] is True
        assert data['status'] == 'waitlisted'

    def test_non_admin_blocked(self, app, client):
        with app.app_context():
            user = make_user(f'notadmin_{id(self)}@test.com', role='client')
            make_client_profile(user.id)
            db.session.commit()
            email = user.email

        login(client, email)
        resp = self._post(client, {
            'dog_id': 1, 'user_id': 1,
            'date': tomorrow().isoformat(), 'slot': 'Morning',
        })
        # Redirected or forbidden — not 200 success
        assert resp.status_code != 200 or b'success' not in resp.data


# ---------------------------------------------------------------------------
# T3d — Admin recurring_for_dog (POST /admin/recurring_for_dog)
# ---------------------------------------------------------------------------

class TestAdminRecurringForDog:

    def _post(self, flask_client, payload):
        return flask_client.post(
            '/admin/recurring_for_dog',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_daily_skips_weekends(self, app, client):
        mon = next_monday()
        sun = mon + datetime.timedelta(days=6)

        with app.app_context():
            # Schedule walker for every weekday so capacity exists Mon–Fri
            walker_email = f'walker_adr_{id(self)}@test.com'
            for dow in range(5):
                if dow == 0:
                    make_walker_with_schedule(walker_email, dow, 'Morning')
                else:
                    from app.models import Walker as WModel
                    w = WModel.query.join(User).filter(User.email == walker_email).first()
                    s = WalkerSchedule(walker_id=w.id, day_of_week=dow, slot='Morning', active=True)
                    db.session.add(s)
                    db.session.flush()
            make_service(capacity=6)
            admin = make_user(f'admin_adr_{id(self)}@test.com', role='walker', is_admin=True)
            client_user = make_user(f'cl_adr_{id(self)}@test.com', role='client')
            dog = make_dog()
            attach_dog(dog.id, client_user.id)
            db.session.commit()
            dog_id = dog.id
            user_id = client_user.id
            admin_email = admin.email

        login(client, admin_email)
        resp = self._post(client, {
            'dog_id': dog_id,
            'user_id': user_id,
            'start_date': mon.isoformat(),
            'end_date': sun.isoformat(),
            'slot': 'Morning',
            'frequency': 'daily',
        })
        data = resp.get_json()
        assert data['success'] is True
        # Mon–Fri = 5 (Sat+Sun skipped)
        assert data['created'] + data['waitlisted'] == 5

    def test_admin_not_capped_at_4_weeks(self, app, client):
        """Admin recurring has no 4-week cap (unlike client recurring)."""
        mon = next_monday()
        eight_weeks = mon + datetime.timedelta(weeks=8)

        with app.app_context():
            make_walker_with_schedule(
                f'walker_nocap_{id(self)}@test.com',
                mon.weekday(), 'Morning'
            )
            make_service(capacity=6)
            admin = make_user(f'admin_nocap_{id(self)}@test.com', role='walker', is_admin=True)
            client_user = make_user(f'cl_nocap_{id(self)}@test.com', role='client')
            dog = make_dog()
            attach_dog(dog.id, client_user.id)
            db.session.commit()
            dog_id = dog.id
            user_id = client_user.id
            admin_email = admin.email

        login(client, admin_email)
        resp = self._post(client, {
            'dog_id': dog_id,
            'user_id': user_id,
            'start_date': mon.isoformat(),
            'end_date': eight_weeks.isoformat(),
            'slot': 'Morning',
            'frequency': 'weekly',
        })
        data = resp.get_json()
        # 9 Mondays over 8 weeks (inclusive)
        assert data['success'] is True
        assert data['created'] + data['waitlisted'] == 9

    def test_duplicate_dates_skipped(self, app, client):
        mon = next_monday()

        with app.app_context():
            make_walker_with_schedule(
                f'walker_skip_{id(self)}@test.com',
                mon.weekday(), 'Morning'
            )
            st = make_service(capacity=6)
            admin = make_user(f'admin_skip_{id(self)}@test.com', role='walker', is_admin=True)
            client_user = make_user(f'cl_skip_{id(self)}@test.com', role='client')
            dog = make_dog()
            attach_dog(dog.id, client_user.id)
            db.session.add(Booking(
                user_id=client_user.id, dog_id=dog.id,
                service_type_id=st.id, date=mon, slot='Morning',
                status='confirmed',
            ))
            db.session.commit()
            dog_id = dog.id
            user_id = client_user.id
            admin_email = admin.email

        login(client, admin_email)
        resp = self._post(client, {
            'dog_id': dog_id,
            'user_id': user_id,
            'start_date': mon.isoformat(),
            'end_date': mon.isoformat(),
            'slot': 'Morning',
            'frequency': 'weekly',
        })
        data = resp.get_json()
        assert data['success'] is True
        assert data['skipped'] == 1
        assert data['created'] == 0
