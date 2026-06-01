"""
Regression tests for the admin walker-schedule modal endpoint:
POST /admin/walkers/<walker_id>/schedule-json

Bug: removing a (weekday, slot) from a walker's WalkerSchedule via the modal
left existing confirmed bookings on that combo attached to the walker. Because
board_data only renders walker lanes for walkers scheduled (or ad-hoc available)
on the date, those bookings became UI-orphans: still confirmed in the DB,
walker_id set to a walker with no lane on that date, so they vanished from the
admin board entirely (not pending, no lane to render under).

Expected behaviour: those bookings get walker_id=None, status='requested',
and each affected client gets one 'system' notification.
"""
import datetime
import json
import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Booking, BookingStatusChange, Client, Dog, DogOwner, Notification,
    ServiceType, User, Walker, WalkerSchedule,
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
            try:
                db.session.execute(text(f'DELETE FROM {table}'))
            except Exception:
                db.session.rollback()
        db.session.commit()
    yield


def _next_weekday(target_dow):
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != target_dow:
        d += datetime.timedelta(days=1)
    return d


def _make_admin(email='admin_sm@test.com'):
    u = User(
        firstname='Admin', lastname='User', email=email,
        role='walker', is_admin=True, active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(u); db.session.flush()
    return u


def _make_walker(email='walker_sm@test.com'):
    u = User(
        firstname='Walker', lastname='Test', email=email,
        role='walker', is_admin=False, active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(u); db.session.flush()
    w = Walker(user_id=u.id); db.session.add(w); db.session.flush()
    return u, w


def _make_client(email='client_sm@test.com'):
    u = User(
        firstname='Client', lastname='User', email=email,
        role='client', active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(u); db.session.flush()
    db.session.add(Client(user_id=u.id, onboarding_completed=True))
    db.session.flush()
    return u


def _make_service_type():
    st = ServiceType(
        name='Group Walk', slug='group-walk',
        capacity_model='walker_assigned',
        slot_type='morning_afternoon',
        requires_walker=True,
        default_max_capacity=6,
        active=True,
    )
    db.session.add(st); db.session.flush()
    return st


def _make_dog(owner_id, name='Buddy'):
    d = Dog(name=name, breed='Labrador')
    db.session.add(d); db.session.flush()
    db.session.add(DogOwner(dog_id=d.id, user_id=owner_id, role='primary'))
    db.session.flush()
    return d


def _confirmed_booking(client_id, dog_id, service_id, walker_id, date_, slot='Morning'):
    b = Booking(
        user_id=client_id, dog_id=dog_id, service_type_id=service_id,
        walker_id=walker_id, date=date_, slot=slot, status='confirmed',
    )
    db.session.add(b); db.session.flush()
    return b


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _post_schedule(flask_client, walker_id, schedules):
    return flask_client.post(
        f'/admin/walkers/{walker_id}/schedule-json',
        data=json.dumps({'schedules': schedules}),
        content_type='application/json',
    )


class TestScheduleModalResetsAffectedBookings:
    """When removing a (weekday, slot) from a walker's schedule, future
    confirmed bookings on that combo must be reset to 'requested' and the
    client must be notified."""

    def test_removed_combo_resets_booking_and_notifies_client(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            client_u = _make_client()
            dog = _make_dog(owner_id=client_u.id)
            st = _make_service_type()
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=1, slot='Morning', active=True,
            ))
            db.session.commit()
            booking = _confirmed_booking(
                client_u.id, dog.id, st.id, walker.id, monday, 'Morning',
            )
            db.session.commit()
            admin_email, admin_id = admin.email, admin.id
            walker_id, booking_id, client_user_id = walker.id, booking.id, client_u.id

        _login(client, admin_email)
        # New schedule: keep Tuesday Morning, drop Monday Morning.
        resp = _post_schedule(client, walker_id, [{'day': 1, 'slot': 'Morning'}])

        assert resp.status_code == 200, resp.get_json()
        payload = resp.get_json()
        assert payload['success'] is True
        assert payload['affected_count'] == 1

        with app.app_context():
            b = db.session.get(Booking, booking_id)
            assert b.walker_id is None, "walker_id should be cleared"
            assert b.status == 'requested', "status should be reset to requested"

            # Session 1: the reset writes a BSC row attributed to the admin.
            rows = BookingStatusChange.query.filter_by(booking_id=booking_id).all()
            assert len(rows) == 1
            assert rows[0].from_status == 'confirmed'
            assert rows[0].to_status == 'requested'
            assert rows[0].changed_by_id == admin_id

            notifs = Notification.query.filter_by(recipient_id=client_user_id).all()
            assert len(notifs) == 1
            n = notifs[0]
            assert n.notification_type == 'system'
            assert "Buddy's" in n.title and "needs a new walker" in n.title
            assert "No action needed" in n.body

    def test_kept_combo_leaves_booking_assigned(self, app, client):
        """A schedule POST that doesn't remove the booking's (weekday, slot)
        must not touch it — guards against over-eager reset."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            client_u = _make_client()
            dog = _make_dog(owner_id=client_u.id)
            st = _make_service_type()
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Afternoon', active=True,
            ))
            db.session.commit()
            booking = _confirmed_booking(
                client_u.id, dog.id, st.id, walker.id, monday, 'Morning',
            )
            db.session.commit()
            admin_email = admin.email
            walker_id, booking_id, client_user_id = walker.id, booking.id, client_u.id

        _login(client, admin_email)
        # Drop only Monday Afternoon — booking is on Monday Morning, unaffected.
        resp = _post_schedule(client, walker_id, [{'day': 0, 'slot': 'Morning'}])

        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['affected_count'] == 0

        with app.app_context():
            b = db.session.get(Booking, booking_id)
            assert b.walker_id == walker_id
            assert b.status == 'confirmed'
            assert Notification.query.filter_by(recipient_id=client_user_id).count() == 0

    def test_past_bookings_unaffected(self, app, client):
        """Bookings in the past must not be touched (history is immutable)."""
        past_monday = datetime.date.today() - datetime.timedelta(
            days=(datetime.date.today().weekday() + 7)
        )
        # Ensure it's actually in the past and a Monday
        assert past_monday < datetime.date.today()
        assert past_monday.weekday() == 0

        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            client_u = _make_client()
            dog = _make_dog(owner_id=client_u.id)
            st = _make_service_type()
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.commit()
            booking = _confirmed_booking(
                client_u.id, dog.id, st.id, walker.id, past_monday, 'Morning',
            )
            db.session.commit()
            admin_email = admin.email
            walker_id, booking_id = walker.id, booking.id

        _login(client, admin_email)
        # Drop Monday Morning entirely.
        resp = _post_schedule(client, walker_id, [])

        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['affected_count'] == 0

        with app.app_context():
            b = db.session.get(Booking, booking_id)
            assert b.walker_id == walker_id
            assert b.status == 'confirmed'

    def test_multiple_clients_each_get_one_notification(self, app, client):
        """If 3 bookings across 2 clients are affected, each client gets
        exactly one notification carrying their own per-client count."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            client_a = _make_client(email='client_a@test.com')
            client_b = _make_client(email='client_b@test.com')
            dog_a1 = _make_dog(owner_id=client_a.id, name='AlphaOne')
            dog_a2 = _make_dog(owner_id=client_a.id, name='AlphaTwo')
            dog_b1 = _make_dog(owner_id=client_b.id, name='BetaOne')
            st = _make_service_type()
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.commit()
            _confirmed_booking(client_a.id, dog_a1.id, st.id, walker.id, monday)
            _confirmed_booking(client_a.id, dog_a2.id, st.id, walker.id, monday)
            _confirmed_booking(client_b.id, dog_b1.id, st.id, walker.id, monday)
            db.session.commit()
            admin_email = admin.email
            walker_id = walker.id
            a_id, b_id = client_a.id, client_b.id

        _login(client, admin_email)
        resp = _post_schedule(client, walker_id, [])

        assert resp.get_json()['affected_count'] == 3
        with app.app_context():
            a_notifs = Notification.query.filter_by(recipient_id=a_id).all()
            b_notifs = Notification.query.filter_by(recipient_id=b_id).all()
            assert len(a_notifs) == 1
            assert len(b_notifs) == 1
            # Client A has 2 bookings across 2 dogs → grouped "2 of your walks…"
            assert "2 of your walks" in a_notifs[0].title
            # Client B has 1 booking → single-item "BetaOne's … needs a new walker"
            assert "BetaOne's" in b_notifs[0].title and "needs a new walker" in b_notifs[0].title

            # Session 1: all 3 resets share one batch_id so the feed can cluster
            # them, and each is a distinct confirmed→requested BSC row.
            rows = BookingStatusChange.query.all()
            assert len(rows) == 3
            assert all(r.from_status == 'confirmed' and r.to_status == 'requested'
                       for r in rows)
            batch_ids = {r.batch_id for r in rows}
            assert len(batch_ids) == 1 and None not in batch_ids
