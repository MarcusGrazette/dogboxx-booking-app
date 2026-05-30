"""Session 1 — BookingStatusChange action-log foundation.

Every booking status transition must route through app.utils.booking_status and
append exactly one BookingStatusChange (BSC) row with the correct
from_status / to_status / changed_by_id (see NOTIFICATIONS.md §9.8).

This module has two layers:

  TestTransitionHelpers — unit tests of the helpers in isolation (timestamps,
  cancelled_by, walker_id, batch_id, from/to).

  TestRouteWiring — integration tests proving each transition site is actually
  wired to the helpers, asserting the resulting BSC rows + actor attribution.
"""
import datetime
import json
import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import (
    User, Client, Dog, DogOwner, Walker, WalkerSchedule, WalkerUnavailability,
    ServiceType, Booking, BookingStatusChange,
)
from app.utils.booking_status import (
    transition_booking, record_booking_created, bulk_transition,
)


TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'walker_schedules', 'walker_unavailabilities',
    'walker_adhoc_availability', 'walkers', 'clients', 'service_types', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield


# ---------------------------------------------------------------------------
# Date + DB helpers (mirrors test_bookings.py conventions)
# ---------------------------------------------------------------------------

def tomorrow():
    return datetime.date.today() + datetime.timedelta(days=1)


def make_user(email, role='client', is_admin=False):
    u = User(
        firstname='Test', lastname='User', email=email, role=role,
        is_admin=is_admin, active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(u)
    db.session.flush()
    return u


def make_client_profile(user_id):
    db.session.add(Client(user_id=user_id, onboarding_completed=True))
    db.session.flush()


def make_dog(name='Buddy'):
    d = Dog(name=name, breed='Labrador')
    db.session.add(d)
    db.session.flush()
    return d


def attach_dog(dog_id, user_id, role='primary'):
    db.session.add(DogOwner(dog_id=dog_id, user_id=user_id, role=role))
    db.session.flush()


def make_walker_with_schedule(email, day_of_week, slot):
    u = make_user(email, role='walker')
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    db.session.add(WalkerSchedule(
        walker_id=w.id, day_of_week=day_of_week, slot=slot, active=True,
    ))
    db.session.flush()
    return w


def make_service(slug='group-walk', name='Group Walk', capacity=6):
    st = ServiceType(
        name=name, slug=slug, capacity_model='walker_assigned',
        slot_type='morning_afternoon', requires_walker=True,
        default_max_capacity=capacity, active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


def login(flask_client, email, password='Testpass1!'):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': password,
    }, follow_redirects=True)


def bsc_rows(booking_id):
    """All BSC rows for a booking, oldest first."""
    return (BookingStatusChange.query
            .filter_by(booking_id=booking_id)
            .order_by(BookingStatusChange.created_at, BookingStatusChange.id)
            .all())


def seed_booking(status='requested', date=None, slot='Morning'):
    """Create a client + dog + service + one Booking. Returns (booking, client_user)."""
    user = make_user(f'cl_{id(object())}@test.com', role='client')
    make_client_profile(user.id)
    dog = make_dog()
    attach_dog(dog.id, user.id)
    st = make_service()
    b = Booking(
        user_id=user.id, dog_id=dog.id, service_type_id=st.id,
        date=date or tomorrow(), slot=slot, status=status,
    )
    db.session.add(b)
    db.session.flush()
    return b, user


# ---------------------------------------------------------------------------
# Layer 1 — helper unit tests
# ---------------------------------------------------------------------------

class TestTransitionHelpers:

    def test_record_booking_created_writes_initial_row(self, app):
        with app.app_context():
            b, user = seed_booking(status='requested')
            row = record_booking_created(b, actor_id=user.id)
            db.session.commit()
            assert row.from_status is None
            assert row.to_status == 'requested'
            assert row.changed_by_id == user.id
            assert row.batch_id is None

    def test_record_booking_created_captures_waitlisted(self, app):
        with app.app_context():
            b, user = seed_booking(status='waitlisted')
            row = record_booking_created(b, actor_id=user.id)
            db.session.commit()
            assert row.from_status is None
            assert row.to_status == 'waitlisted'

    def test_transition_to_confirmed_sets_confirmed_at_and_walker(self, app):
        with app.app_context():
            walker = make_walker_with_schedule(
                'w_conf@test.com', tomorrow().weekday(), 'Morning')
            b, user = seed_booking(status='requested')
            assert b.confirmed_at is None
            row = transition_booking(b, 'confirmed', actor_id=user.id,
                                     walker_id=walker.id)
            db.session.commit()
            assert b.status == 'confirmed'
            assert b.confirmed_at is not None
            assert b.walker_id == walker.id
            assert row.from_status == 'requested'
            assert row.to_status == 'confirmed'
            assert row.changed_by_id == user.id

    def test_transition_to_cancelled_sets_cancelled_fields(self, app):
        with app.app_context():
            b, user = seed_booking(status='confirmed')
            row = transition_booking(b, 'cancelled', actor_id=user.id,
                                     cancelled_by='client', walker_id=None)
            db.session.commit()
            assert b.status == 'cancelled'
            assert b.cancelled_at is not None
            assert b.cancelled_by == 'client'
            assert b.walker_id is None
            assert row.from_status == 'confirmed'
            assert row.to_status == 'cancelled'

    def test_transition_to_rejected_sets_cancelled_at(self, app):
        with app.app_context():
            b, user = seed_booking(status='requested')
            transition_booking(b, 'rejected', actor_id=user.id, cancelled_by='admin')
            db.session.commit()
            assert b.status == 'rejected'
            assert b.cancelled_at is not None
            assert b.cancelled_by == 'admin'

    def test_reset_does_not_clear_confirmed_at(self, app):
        """Reset (confirmed→requested) leaves confirmed_at intact (no behaviour change)."""
        with app.app_context():
            b, user = seed_booking(status='confirmed')
            b.confirmed_at = datetime.datetime.now(datetime.timezone.utc)
            db.session.flush()
            transition_booking(b, 'requested', actor_id=user.id, walker_id=None)
            db.session.commit()
            assert b.status == 'requested'
            assert b.walker_id is None
            assert b.confirmed_at is not None  # preserved

    def test_walker_id_unset_leaves_walker_untouched(self, app):
        with app.app_context():
            walker = make_walker_with_schedule(
                'w_untouched@test.com', tomorrow().weekday(), 'Morning')
            b, user = seed_booking(status='confirmed')
            b.walker_id = walker.id
            db.session.flush()
            # No walker_id kwarg → must not change it.
            transition_booking(b, 'cancelled', actor_id=user.id, cancelled_by='admin')
            db.session.commit()
            assert b.walker_id == walker.id

    def test_bulk_transition_one_row_each_shared_batch(self, app):
        with app.app_context():
            user = make_user('bulk@test.com', role='client')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            st = make_service()
            bookings = []
            for i in range(3):
                b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=st.id,
                            date=tomorrow() + datetime.timedelta(days=i),
                            slot='Morning', status='confirmed')
                db.session.add(b)
                bookings.append(b)
            db.session.flush()

            rows = bulk_transition(bookings, 'cancelled', actor_id=user.id,
                                   walker_id=None, cancelled_by='admin',
                                   batch_id='shared-batch')
            db.session.commit()
            assert len(rows) == 3
            assert all(r.batch_id == 'shared-batch' for r in rows)
            assert all(r.to_status == 'cancelled' for r in rows)
            assert all(b.status == 'cancelled' for b in bookings)


# ---------------------------------------------------------------------------
# Layer 2 — route wiring
# ---------------------------------------------------------------------------

class TestCreationTransitions:

    def test_client_one_off_auto_confirmed_logs_create_and_confirm(self, app, client):
        """Client books, walker available → create(requested) + confirm(confirmed)."""
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule('w_auto@test.com', tom.weekday(), 'Morning')
            make_service(capacity=6)
            user = make_user('client_auto@test.com', role='client')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()
            email, uid = user.email, user.id

        login(client, email)
        client.post('/', data={'date': tomorrow().isoformat(), 'slot': 'Morning'},
                    follow_redirects=True)

        with app.app_context():
            booking = Booking.query.filter_by(status='confirmed').first()
            assert booking is not None
            rows = bsc_rows(booking.id)
            assert [r.to_status for r in rows] == ['requested', 'confirmed']
            assert rows[0].from_status is None
            assert rows[1].from_status == 'requested'
            # Actor on both is the booking creator (the client).
            assert all(r.changed_by_id == uid for r in rows)

    def test_full_slot_logs_create_waitlisted(self, app, client):
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule('w_wl@test.com', tom.weekday(), 'Morning')
            st = make_service(capacity=1)
            filler = make_user('filler_wl@test.com', role='client')
            fdog = make_dog('Filler')
            attach_dog(fdog.id, filler.id)
            db.session.add(Booking(
                user_id=filler.id, dog_id=fdog.id, service_type_id=st.id,
                date=tom, slot='Morning', status='confirmed'))
            user = make_user('client_wl@test.com', role='client')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()
            email, uid = user.email, user.id

        login(client, email)
        client.post('/', data={'date': tomorrow().isoformat(), 'slot': 'Morning'},
                    follow_redirects=True)

        with app.app_context():
            booking = Booking.query.filter_by(user_id=uid, status='waitlisted').first()
            assert booking is not None
            rows = bsc_rows(booking.id)
            assert len(rows) == 1
            assert rows[0].from_status is None
            assert rows[0].to_status == 'waitlisted'
            assert rows[0].changed_by_id == uid

    def test_drop_in_logs_create_requested(self, app, client):
        with app.app_context():
            tom = tomorrow()
            # A drop-in-capable walker so capacity check passes.
            wu = make_user('w_drop@test.com', role='walker')
            w = Walker(user_id=wu.id, does_drop_ins=True)
            db.session.add(w)
            db.session.flush()
            db.session.add(WalkerSchedule(
                walker_id=w.id, day_of_week=tom.weekday(), slot='Morning', active=True))
            make_service(slug='drop-in', name='Drop In', capacity=6)
            user = make_user('client_drop@test.com', role='client')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()
            email, uid, dog_id = user.email, user.id, dog.id

        login(client, email)
        client.post('/book_drop_in', data=json.dumps({
            'date': tomorrow().isoformat(), 'slot': 'Morning', 'dog_id': dog_id,
        }), content_type='application/json')

        with app.app_context():
            booking = Booking.query.filter_by(user_id=uid).first()
            assert booking is not None
            rows = bsc_rows(booking.id)
            assert len(rows) == 1
            assert rows[0].from_status is None
            assert rows[0].to_status == booking.status  # requested (drop-ins never auto-confirm)
            assert rows[0].changed_by_id == uid

    def test_admin_book_for_dog_confirmed_logs_rows_with_admin_actor(self, app, client):
        with app.app_context():
            tom = tomorrow()
            make_walker_with_schedule('w_abfd@test.com', tom.weekday(), 'Morning')
            make_service(capacity=6)
            admin = make_user('admin_abfd@test.com', role='walker', is_admin=True)
            cu = make_user('cl_abfd@test.com', role='client')
            make_client_profile(cu.id)
            dog = make_dog()
            attach_dog(dog.id, cu.id)
            db.session.commit()
            admin_email, admin_id, dog_id, uid = admin.email, admin.id, dog.id, cu.id

        login(client, admin_email)
        client.post('/admin/book_for_dog', data=json.dumps({
            'dog_id': dog_id, 'user_id': uid,
            'date': tomorrow().isoformat(), 'slot': 'Morning',
        }), content_type='application/json')

        with app.app_context():
            booking = Booking.query.filter_by(dog_id=dog_id).first()
            assert booking.status == 'confirmed'
            rows = bsc_rows(booking.id)
            assert [r.to_status for r in rows] == ['requested', 'confirmed']
            # Admin is the actor for an admin-initiated booking, and the batch_id
            # is shared across the action's rows.
            assert all(r.changed_by_id == admin_id for r in rows)
            assert rows[0].batch_id is not None
            assert rows[0].batch_id == rows[1].batch_id


class TestConfirmReject:

    def _assign(self, flask_client, booking_id, walker_id):
        return flask_client.post('/admin/assign_walker', data=json.dumps({
            'booking_id': booking_id, 'walker_id': walker_id,
        }), content_type='application/json')

    def test_assign_walker_logs_confirm_with_admin_actor(self, app, client):
        with app.app_context():
            tom = tomorrow()
            walker = make_walker_with_schedule('w_assign@test.com', tom.weekday(), 'Morning')
            admin = make_user('admin_assign@test.com', role='walker', is_admin=True)
            b, _ = seed_booking(status='requested', date=tom)
            db.session.commit()
            admin_email, admin_id = admin.email, admin.id
            bid, wid = b.id, walker.id

        login(client, admin_email)
        resp = self._assign(client, bid, wid)
        assert resp.status_code == 200

        with app.app_context():
            rows = bsc_rows(bid)
            assert len(rows) == 1
            assert rows[0].from_status == 'requested'
            assert rows[0].to_status == 'confirmed'
            assert rows[0].changed_by_id == admin_id

    def test_unassign_logs_reset_to_requested(self, app, client):
        with app.app_context():
            tom = tomorrow()
            walker = make_walker_with_schedule('w_unassign@test.com', tom.weekday(), 'Morning')
            admin = make_user('admin_unassign@test.com', role='walker', is_admin=True)
            b, _ = seed_booking(status='confirmed', date=tom)
            b.walker_id = walker.id
            db.session.commit()
            admin_email, admin_id, bid = admin.email, admin.id, b.id

        login(client, admin_email)
        resp = client.post('/admin/assign_walker', data=json.dumps({
            'booking_id': bid, 'walker_id': None,
        }), content_type='application/json')
        assert resp.status_code == 200

        with app.app_context():
            rows = bsc_rows(bid)
            assert len(rows) == 1
            assert rows[0].from_status == 'confirmed'
            assert rows[0].to_status == 'requested'
            assert rows[0].changed_by_id == admin_id
            assert db.session.get(Booking, bid).walker_id is None

    def test_decline_logs_rejected(self, app, client):
        with app.app_context():
            admin = make_user('admin_decline@test.com', role='walker', is_admin=True)
            b, _ = seed_booking(status='requested')
            db.session.commit()
            admin_email, admin_id, bid = admin.email, admin.id, b.id

        login(client, admin_email)
        resp = client.post(f'/admin/booking/{bid}/decline')
        assert resp.status_code == 200

        with app.app_context():
            rows = bsc_rows(bid)
            assert len(rows) == 1
            assert rows[0].from_status == 'requested'
            assert rows[0].to_status == 'rejected'
            assert rows[0].changed_by_id == admin_id
            assert db.session.get(Booking, bid).cancelled_by == 'admin'


class TestCancel:

    def test_client_cancel_logs_cancelled_by_client(self, app, client):
        with app.app_context():
            b, user = seed_booking(status='confirmed')
            db.session.commit()
            email, uid, bid = user.email, user.id, b.id

        login(client, email)
        resp = client.post('/cancel_booking', data=json.dumps({'booking_id': bid}),
                           content_type='application/json')
        assert resp.status_code == 200

        with app.app_context():
            rows = bsc_rows(bid)
            assert len(rows) == 1
            assert rows[0].from_status == 'confirmed'
            assert rows[0].to_status == 'cancelled'
            assert rows[0].changed_by_id == uid
            assert db.session.get(Booking, bid).cancelled_by == 'client'

    def test_admin_cancel_logs_cancelled_by_admin(self, app, client):
        with app.app_context():
            admin = make_user('admin_cancel@test.com', role='walker', is_admin=True)
            b, _ = seed_booking(status='confirmed')
            db.session.commit()
            admin_email, admin_id, bid = admin.email, admin.id, b.id

        login(client, admin_email)
        resp = client.post('/cancel_booking', data=json.dumps({'booking_id': bid}),
                           content_type='application/json')
        assert resp.status_code == 200

        with app.app_context():
            rows = bsc_rows(bid)
            assert len(rows) == 1
            assert rows[0].to_status == 'cancelled'
            assert rows[0].changed_by_id == admin_id
            assert db.session.get(Booking, bid).cancelled_by == 'admin'


class TestReset:

    def test_admin_unavailability_resets_and_logs(self, app, client):
        """Marking a walker unavailable resets their confirmed bookings, one BSC row each."""
        with app.app_context():
            tom = tomorrow()
            walker = make_walker_with_schedule('w_reset@test.com', tom.weekday(), 'Morning')
            admin = make_user('admin_reset@test.com', role='walker', is_admin=True)
            b, _ = seed_booking(status='confirmed', date=tom)
            b.walker_id = walker.id
            db.session.commit()
            admin_email, admin_id, bid, wid = admin.email, admin.id, b.id, walker.id

        login(client, admin_email)
        resp = client.post(f'/admin/walkers/{wid}/unavailability', data=json.dumps({
            'date': tom.isoformat(), 'slot': 'Morning',
        }), content_type='application/json')
        assert resp.status_code == 201

        with app.app_context():
            rows = bsc_rows(bid)
            assert len(rows) == 1
            assert rows[0].from_status == 'confirmed'
            assert rows[0].to_status == 'requested'
            assert rows[0].changed_by_id == admin_id
            reset = db.session.get(Booking, bid)
            assert reset.walker_id is None
            assert reset.status == 'requested'
