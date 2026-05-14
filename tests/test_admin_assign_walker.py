"""
Regression tests for /admin/assign_walker schedule-gate logic.

Two bugs, one fix (delegating to get_available_walkers()):

1. Ad-hoc availability (reported bug): a walker available only via
   WalkerAdHocAvailability — no default WalkerSchedule row — was incorrectly
   rejected because the old inline check never queried the adhoc table.

2. Unavailability message (latent bug): a walker with a default schedule but a
   WalkerUnavailability entry for the specific date passed the gate silently;
   the admin had no way to know the walker was marked off.
"""
import datetime
import json
import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Booking, Client, Dog, DogOwner, ServiceType, User, Walker,
    WalkerAdHocAvailability, WalkerSchedule, WalkerUnavailability,
)

TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'walker_unavailabilities', 'walker_adhoc_availability',
    'walker_schedules', 'dog_owners', 'dogs', 'clients',
    'service_types', 'walkers', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield


def _next_weekday(target_dow):
    """Return the nearest future date whose weekday() == target_dow (0=Mon)."""
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != target_dow:
        d += datetime.timedelta(days=1)
    return d


def _make_admin(email='admin_aw@test.com'):
    u = User(
        firstname='Admin', lastname='User', email=email,
        role='walker', is_admin=True, active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(u)
    db.session.flush()
    return u


def _make_walker(email='walker_aw@test.com'):
    u = User(
        firstname='Walker', lastname='Test', email=email,
        role='walker', is_admin=False, active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(u)
    db.session.flush()
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    return u, w


def _make_booking(date, slot='Morning'):
    """Seed a minimal client + dog + service type + requested booking."""
    client_u = User(
        firstname='Client', lastname='User', email='client_aw@test.com',
        role='client', active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(client_u)
    db.session.flush()
    db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
    db.session.flush()

    dog = Dog(name='Buddy', breed='Labrador')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=client_u.id, role='primary'))
    db.session.flush()

    st = ServiceType(
        name='Group Walk', slug='group-walk',
        capacity_model='walker_assigned',
        slot_type='morning_afternoon',
        requires_walker=True,
        default_max_capacity=6,
        active=True,
    )
    db.session.add(st)
    db.session.flush()

    booking = Booking(
        user_id=client_u.id,
        dog_id=dog.id,
        service_type_id=st.id,
        date=date,
        slot=slot,
        status='requested',
    )
    db.session.add(booking)
    db.session.flush()
    db.session.commit()
    return booking


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _post_assign(flask_client, booking_id, walker_id, **extra):
    payload = {'booking_id': booking_id, 'walker_id': walker_id, **extra}
    return flask_client.post(
        '/admin/assign_walker',
        data=json.dumps(payload),
        content_type='application/json',
    )


class TestAdHocAvailability:
    """Regression: walker available only via ad-hoc entry was incorrectly rejected."""

    def test_adhoc_only_walker_can_be_assigned(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking = _make_booking(monday, slot='Morning')
            # No WalkerSchedule row — availability comes entirely from ad-hoc entry.
            db.session.add(WalkerAdHocAvailability(
                walker_id=walker.id, date=monday, slot='Morning',
            ))
            db.session.commit()
            admin_email, booking_id, walker_id = admin.email, booking.id, walker.id

        _login(client, admin_email)
        resp = _post_assign(client, booking_id, walker_id)
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['success'] is True


class TestUnavailabilityMessage:
    """Latent bug: scheduled-but-unavailable walker should give a clear error, not a generic one."""

    def test_scheduled_but_unavailable_gives_marked_unavailable_message(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking = _make_booking(monday, slot='Morning')
            # Default schedule covers Monday Morning...
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            # ...but walker is marked off for this specific date.
            db.session.add(WalkerUnavailability(
                walker_id=walker.id, date=monday, slot='Morning',
            ))
            db.session.commit()
            admin_email, booking_id, walker_id = admin.email, booking.id, walker.id

        _login(client, admin_email)
        resp = _post_assign(client, booking_id, walker_id)
        data = resp.get_json()
        assert resp.status_code == 400
        assert 'marked unavailable' in data['message']
