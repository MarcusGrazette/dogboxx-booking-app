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
import threading
import time
import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app import db
from app.blueprints.admin.views import board as board_module
from app.models import (
    Booking, BookingStatusChange, Client, Dog, DogOwner, ServiceType, User, Walker,
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


def _make_booking(date, slot='Morning', email='client_aw@test.com', dog_name='Buddy'):
    """Seed a minimal client + dog + service type + requested booking.

    Reuses the 'Group Walk' service type across calls within a test (idempotent
    on slug) so a test can build several bookings for the same date/slot.
    """
    client_u = User(
        firstname='Client', lastname='User', email=email,
        role='client', active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(client_u)
    db.session.flush()
    db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
    db.session.flush()

    dog = Dog(name=dog_name, breed='Labrador')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=client_u.id, role='primary'))
    db.session.flush()

    st = ServiceType.query.filter_by(slug='group-walk').first()
    if not st:
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


class TestAssignWalkerActionLog:
    """Session 1: assignment/unassignment must write a BookingStatusChange row."""

    def test_assign_logs_confirm_with_admin_actor(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking = _make_booking(monday, slot='Morning')
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.commit()
            admin_email, admin_id = admin.email, admin.id
            booking_id, walker_id = booking.id, walker.id

        _login(client, admin_email)
        resp = _post_assign(client, booking_id, walker_id)
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            rows = (BookingStatusChange.query
                    .filter_by(booking_id=booking_id)
                    .order_by(BookingStatusChange.id).all())
            assert len(rows) == 1
            assert rows[0].from_status == 'requested'
            assert rows[0].to_status == 'confirmed'
            assert rows[0].changed_by_id == admin_id

    def test_unassign_logs_reset_with_admin_actor(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking = _make_booking(monday, slot='Morning')
            booking.status = 'confirmed'
            booking.walker_id = walker.id
            db.session.commit()
            admin_email, admin_id, booking_id = admin.email, admin.id, booking.id

        _login(client, admin_email)
        resp = _post_assign(client, booking_id, None)
        assert resp.status_code == 200

        with app.app_context():
            rows = (BookingStatusChange.query
                    .filter_by(booking_id=booking_id)
                    .order_by(BookingStatusChange.id).all())
            assert len(rows) == 1
            assert rows[0].from_status == 'confirmed'
            assert rows[0].to_status == 'requested'
            assert rows[0].changed_by_id == admin_id


class TestPickupOrderOnManualAssign:
    """Manual click-to-assign now sequences pickup_order to the end of the
    walker's lane, mirroring the auto-confirm path in booking_service.py —
    previously only auto-confirm ever wrote this column, so a manually
    assigned booking's pickup_order stayed NULL forever."""

    def test_first_assign_in_lane_gets_order_one(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking = _make_booking(monday, slot='Morning')
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.commit()
            admin_email, booking_id, walker_id = admin.email, booking.id, walker.id

        _login(client, admin_email)
        resp = _post_assign(client, booking_id, walker_id)
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            assert db.session.get(Booking, booking_id).pickup_order == 1

    def test_second_assign_in_same_lane_appends_to_end(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking_a = _make_booking(monday, slot='Morning',
                                       email='client_a@test.com', dog_name='Buddy')
            booking_b = _make_booking(monday, slot='Morning',
                                       email='client_b@test.com', dog_name='Rex')
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.commit()
            admin_email = admin.email
            booking_a_id, booking_b_id, walker_id = booking_a.id, booking_b.id, walker.id

        _login(client, admin_email)
        assert _post_assign(client, booking_a_id, walker_id).status_code == 200
        assert _post_assign(client, booking_b_id, walker_id).status_code == 200

        with app.app_context():
            assert db.session.get(Booking, booking_a_id).pickup_order == 1
            assert db.session.get(Booking, booking_b_id).pickup_order == 2

    def test_reassign_to_different_walker_appends_to_new_lane(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker_a = _make_walker(email='walker_a_aw@test.com')
            _, walker_b = _make_walker(email='walker_b_aw@test.com')
            booking_x = _make_booking(monday, slot='Morning',
                                       email='client_x@test.com', dog_name='Buddy')
            booking_sib = _make_booking(monday, slot='Morning',
                                         email='client_sib@test.com', dog_name='Rex')
            for w in (walker_a, walker_b):
                db.session.add(WalkerSchedule(
                    walker_id=w.id, day_of_week=0, slot='Morning', active=True,
                ))
            db.session.commit()
            admin_email = admin.email
            booking_x_id, booking_sib_id = booking_x.id, booking_sib.id
            walker_a_id, walker_b_id = walker_a.id, walker_b.id

        _login(client, admin_email)
        # booking_x starts on walker_a's lane; booking_sib already occupies
        # position 1 on walker_b's lane before the reassignment.
        assert _post_assign(client, booking_x_id, walker_a_id).status_code == 200
        assert _post_assign(client, booking_sib_id, walker_b_id).status_code == 200
        resp = _post_assign(client, booking_x_id, walker_b_id)
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            assert db.session.get(Booking, booking_sib_id).pickup_order == 1
            assert db.session.get(Booking, booking_x_id).pickup_order == 2

    def test_slot_override_appends_to_new_slot_lane(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking_x = _make_booking(monday, slot='Morning',
                                       email='client_x@test.com', dog_name='Buddy')
            booking_sib = _make_booking(monday, slot='Afternoon',
                                         email='client_sib@test.com', dog_name='Rex')
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Afternoon', active=True,
            ))
            db.session.commit()
            admin_email = admin.email
            booking_x_id, booking_sib_id, walker_id = booking_x.id, booking_sib.id, walker.id

        _login(client, admin_email)
        assert _post_assign(client, booking_x_id, walker_id).status_code == 200
        assert _post_assign(client, booking_sib_id, walker_id).status_code == 200
        # Move booking_x from Morning into the Afternoon lane, which already
        # has booking_sib at position 1.
        resp = _post_assign(client, booking_x_id, walker_id,
                             slot='Afternoon', slot_override=True)
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            assert db.session.get(Booking, booking_sib_id).pickup_order == 1
            assert db.session.get(Booking, booking_x_id).pickup_order == 2

    def test_drop_in_service_type_gets_pickup_order(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            walker.does_drop_ins = True
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.add(ServiceType(
                name='Drop In', slug=ServiceType.DROP_IN,
                capacity_model='walker_assigned',
                slot_type='morning_afternoon',
                requires_walker=True,
                default_max_capacity=6,
                active=True,
            ))
            db.session.flush()
            drop_in_st = ServiceType.query.filter_by(slug=ServiceType.DROP_IN).first()
            booking = _make_booking(monday, slot='Morning')
            booking.service_type_id = drop_in_st.id
            db.session.commit()
            admin_email, booking_id, walker_id = admin.email, booking.id, walker.id

        _login(client, admin_email)
        resp = _post_assign(client, booking_id, walker_id)
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            assert db.session.get(Booking, booking_id).pickup_order == 1

    def test_unassign_then_reassign_still_lands_at_end(self, app, client):
        """Unassign clears pickup_order rather than leaving it stale; a
        reassign back into the same lane recomputes it fresh. Uses the
        *last* booking in the lane deliberately — siblings are never
        renumbered, so unassigning+reassigning the *first* one would collide
        with the still-numbered survivor rather than cleanly land at the end.
        That collision is a known, accepted limit of "append only" ordering,
        not something this fix (or this test) tries to solve."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking_a = _make_booking(monday, slot='Morning',
                                       email='client_a@test.com', dog_name='Buddy')
            booking_b = _make_booking(monday, slot='Morning',
                                       email='client_b@test.com', dog_name='Rex')
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.commit()
            admin_email = admin.email
            booking_a_id, booking_b_id, walker_id = booking_a.id, booking_b.id, walker.id

        _login(client, admin_email)
        assert _post_assign(client, booking_a_id, walker_id).status_code == 200
        assert _post_assign(client, booking_b_id, walker_id).status_code == 200

        # Unassign booking_b (the last one in the lane) — pickup_order must
        # be cleared, not left stale.
        assert _post_assign(client, booking_b_id, None).status_code == 200
        with app.app_context():
            assert db.session.get(Booking, booking_a_id).pickup_order == 1
            assert db.session.get(Booking, booking_b_id).pickup_order is None

        # Re-assign booking_b to the same walker/slot — it should land back
        # at the end of the lane, behind booking_a.
        resp = _post_assign(client, booking_b_id, walker_id)
        assert resp.status_code == 200, resp.get_json()
        with app.app_context():
            assert db.session.get(Booking, booking_a_id).pickup_order == 1
            assert db.session.get(Booking, booking_b_id).pickup_order == 2


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


class TestCapacityExcludesRejectedAndWaitlisted:
    """H3 regression: the capacity check must count Booking.CAPACITY_STATUSES,
    not "anything != cancelled" — otherwise a rejected/waitlisted booking still
    occupies a walker's slot, and an admin declining bookings on a busy day
    permanently locks that walker out of the rest of the day."""

    def test_rejected_booking_does_not_consume_capacity(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            new_booking = _make_booking(monday, slot='Morning')
            # Capacity of 1 so a single non-counting booking is enough to prove the fix.
            new_booking.service_type.default_max_capacity = 1
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            # A different dog — the (dog_id, date, slot) unique index means the
            # rejected booking can't share new_booking's dog for this repro.
            other_client = User(
                firstname='Other', lastname='Client', email='other_aw@test.com',
                role='client', active=True,
                hashed_password=generate_password_hash('Testpass1!'),
            )
            db.session.add(other_client)
            db.session.flush()
            db.session.add(Client(user_id=other_client.id, onboarding_completed=True))
            other_dog = Dog(name='Rex', breed='Poodle')
            db.session.add(other_dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=other_dog.id, user_id=other_client.id, role='primary'))
            db.session.add(Booking(
                user_id=other_client.id, dog_id=other_dog.id,
                service_type_id=new_booking.service_type_id,
                date=monday, slot='Morning', status='rejected', walker_id=walker.id,
            ))
            db.session.commit()
            admin_email, booking_id, walker_id = admin.email, new_booking.id, walker.id

        _login(client, admin_email)
        resp = _post_assign(client, booking_id, walker_id, slot='Morning')
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['success'] is True


class TestSlotOverrideJsonOnly:
    """H4 regression: assign_walker must be JSON-only. It previously fell back
    to request.form, where every value is a string — bool("false") is True in
    Python, so a form-encoded slot_override=false silently bypassed the
    availability check instead of respecting it."""

    def test_form_encoded_body_is_rejected_not_parsed(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_admin()
            _, walker = _make_walker()
            booking = _make_booking(monday, slot='Afternoon')
            # No WalkerSchedule row — walker is not scheduled for Afternoon at all,
            # so this would only succeed if slot_override were misread as True.
            db.session.commit()
            admin_email, booking_id, walker_id = admin.email, booking.id, walker.id

        _login(client, admin_email)
        resp = client.post(
            '/admin/assign_walker',
            data={
                'booking_id': str(booking_id),
                'walker_id': str(walker_id),
                'slot_override': 'false',
            },
        )
        data = resp.get_json()
        assert resp.status_code == 400
        assert data['success'] is False
        assert 'No booking ID' in data['message']


class TestAssignWalkerConcurrencyRace:
    """Regression for the code-review finding (2026-08-23): assign_walker's
    capacity check (same_slot_bookings >= max_capacity) had no advisory lock,
    unlike create_booking(). Two concurrent assignments to the same walker
    could both read the count before either committed, over-filling capacity.

    Fixed by acquiring the same (service, date, slot) advisory lock
    create_booking() takes, right before the capacity check. This test drives
    two real concurrent requests through the actual view and asserts Postgres
    genuinely serializes them — not just that the lock function is called.
    """

    def test_concurrent_assign_calls_do_not_overfill_walker(self, app):
        if db.engine.dialect.name != 'postgresql':
            pytest.skip('advisory lock is a no-op on SQLite — race cannot be exercised')

        monday = _next_weekday(0)
        with app.app_context():
            # Capacity 1 so a second assignment to the same walker/slot must
            # be rejected — any overfill shows up as 2 confirmed bookings.
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned', slot_type='morning_afternoon',
                requires_walker=True, default_max_capacity=1, active=True,
            )
            db.session.add(st)
            db.session.flush()

            admin = _make_admin()
            _, walker = _make_walker()
            db.session.add(WalkerSchedule(
                walker_id=walker.id, day_of_week=0, slot='Morning', active=True,
            ))
            db.session.commit()

            # _make_booking() reuses the 'group-walk' ServiceType created above
            # (capacity 1) rather than creating its own default-capacity-6 one.
            booking_a = _make_booking(monday, slot='Morning', email='race_a@test.com', dog_name='DogA')
            booking_b = _make_booking(monday, slot='Morning', email='race_b@test.com', dog_name='DogB')

            admin_email, walker_id = admin.email, walker.id
            booking_a_id, booking_b_id = booking_a.id, booking_b.id

        # Pause thread A after it has acquired the advisory lock and passed
        # its own capacity check, but before it commits — so the lock (and
        # the uncommitted booking_a.status='confirmed') is still held while
        # thread B tries to run the same check.
        entered_lock = threading.Event()
        release_lock = threading.Event()
        orig_get_walker_slot_count = board_module.get_walker_slot_count

        def _paused_get_walker_slot_count(*a, **kw):
            entered_lock.set()
            release_lock.wait(timeout=5)
            return orig_get_walker_slot_count(*a, **kw)

        board_module.get_walker_slot_count = _paused_get_walker_slot_count

        results = {}

        def _run(name, booking_id):
            c = app.test_client()
            _login(c, admin_email)
            resp = _post_assign(c, booking_id, walker_id, slot='Morning')
            results[name] = (resp.status_code, resp.get_json())

        try:
            t_a = threading.Thread(target=_run, args=('a', booking_a_id))
            t_a.start()
            assert entered_lock.wait(timeout=5), 'thread A never reached the locked section'

            t_b = threading.Thread(target=_run, args=('b', booking_b_id))
            t_b.start()
            # Give B's request time to actually reach and block inside Postgres
            # on the advisory lock — that blocking happens in the DB call, not
            # observable via a Python-level signal.
            time.sleep(0.3)

            release_lock.set()
            t_a.join(timeout=5)
            t_b.join(timeout=5)
        finally:
            board_module.get_walker_slot_count = orig_get_walker_slot_count

        assert not t_a.is_alive() and not t_b.is_alive(), 'a thread never finished — deadlock?'

        status_a, body_a = results['a']
        status_b, body_b = results['b']
        assert status_a == 200, body_a
        assert status_b == 400, body_b
        assert 'maximum bookings' in body_b['message'], body_b

        with app.app_context():
            confirmed = Booking.query.filter(
                Booking.walker_id == walker_id, Booking.date == monday,
                Booking.slot == 'Morning', Booking.status == 'confirmed',
            ).count()
            assert confirmed == 1, \
                f'walker over capacity: expected 1 confirmed booking, found {confirmed}'
