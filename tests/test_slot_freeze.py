"""FEATURES.md #73 — Freeze auto-confirm for a date+slot.

Covers:
- capacity.is_slot_frozen() — plain (date, slot) equality lookup
- create_booking()'s auto-confirm gate: a frozen slot lands as 'requested'
  even with walker capacity available, but capacity/waitlisting itself is
  unaffected and existing confirmed bookings are untouched
- Admin CRUD (/admin/freezes): add (single + range + 'Both'), skip-existing,
  delete-by-range, and the ActivityLog rows each write
- GET /api/slot_availability carries a `frozen` key per slot
"""
import datetime
import json

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    User, Client, Dog, DogOwner, Walker, WalkerSchedule,
    ServiceType, Booking, SlotFreeze, ActivityLog,
)
from app.capacity import is_slot_frozen
from app.services.booking_service import create_booking


def tomorrow():
    return datetime.date.today() + datetime.timedelta(days=1)


def make_user(email, role='client', is_admin=False):
    u = User(firstname='Test', lastname='User', email=email, role=role,
              is_admin=is_admin, hashed_password=generate_password_hash('Testpass1!'),
              active=True)
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
    db.session.add(DogOwner(dog_id=dog_id, user_id=user_id, role='primary'))
    db.session.flush()


def make_walker_with_schedule(email, day_of_week, slot):
    u = make_user(email, role='walker')
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    db.session.add(WalkerSchedule(walker_id=w.id, day_of_week=day_of_week, slot=slot, active=True))
    db.session.flush()
    return w


def make_service(capacity=6):
    st = ServiceType(
        name='Group Walk', slug='group-walk',
        capacity_model='walker_assigned', slot_type='morning_afternoon',
        requires_walker=True, default_max_capacity=capacity, active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


def login(flask_client, email):
    return flask_client.post('/auth/login',
                              data={'email': email, 'password': 'Testpass1!'},
                              follow_redirects=True)


class TestIsSlotFrozen:

    def test_no_freeze_row_returns_false(self, app):
        with app.app_context():
            d = tomorrow()
            assert is_slot_frozen(d, 'Morning') is False

    def test_matching_freeze_row_returns_true(self, app):
        with app.app_context():
            d = tomorrow()
            db.session.add(SlotFreeze(date=d, slot='Morning', range_id='r1'))
            db.session.commit()
            assert is_slot_frozen(d, 'Morning') is True

    def test_freeze_is_slot_specific(self, app):
        """A Morning freeze must not affect Afternoon on the same date."""
        with app.app_context():
            d = tomorrow()
            db.session.add(SlotFreeze(date=d, slot='Morning', range_id='r1'))
            db.session.commit()
            assert is_slot_frozen(d, 'Afternoon') is False


class TestCreateBookingFreezeGate:
    """Integration: create_booking()'s auto_confirm branch must skip
    auto-assign when the (date, slot) is frozen, landing the booking as
    'requested' instead of 'confirmed' — even with full walker capacity."""

    def test_frozen_slot_lands_as_requested_not_confirmed(self, app):
        d = tomorrow()
        with app.app_context():
            make_walker_with_schedule(f'w_frozen_{id(self)}@test.com', d.weekday(), 'Morning')
            service = make_service()
            user = make_user(f'cl_frozen_{id(self)}@test.com')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.add(SlotFreeze(date=d, slot='Morning', range_id='r1'))
            db.session.commit()

            booking, auto_confirmed = create_booking(
                dog=dog, user_id=user.id, date=d, slot='Morning',
                service=service, actor_id=user.id, batch_id='b1',
            )
            db.session.commit()

            assert auto_confirmed is False
            assert booking.status == 'requested'
            assert booking.walker_id is None

    def test_unfrozen_slot_still_auto_confirms(self, app):
        """Control: without a freeze row, the same setup auto-confirms."""
        d = tomorrow()
        with app.app_context():
            make_walker_with_schedule(f'w_ctrl_{id(self)}@test.com', d.weekday(), 'Morning')
            service = make_service()
            user = make_user(f'cl_ctrl_{id(self)}@test.com')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()

            booking, auto_confirmed = create_booking(
                dog=dog, user_id=user.id, date=d, slot='Morning',
                service=service, actor_id=user.id, batch_id='b1',
            )
            db.session.commit()

            assert auto_confirmed is True
            assert booking.status == 'confirmed'

    def test_freeze_does_not_touch_already_confirmed_bookings(self, app):
        """Freezing a slot after a booking is already confirmed must not
        retroactively change it."""
        d = tomorrow()
        with app.app_context():
            w = make_walker_with_schedule(f'w_existing_{id(self)}@test.com', d.weekday(), 'Morning')
            service = make_service()
            user = make_user(f'cl_existing_{id(self)}@test.com')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.commit()

            booking, auto_confirmed = create_booking(
                dog=dog, user_id=user.id, date=d, slot='Morning',
                service=service, actor_id=user.id, batch_id='b1',
            )
            db.session.commit()
            assert booking.status == 'confirmed'
            booking_id = booking.id

            # Freeze added after the fact
            db.session.add(SlotFreeze(date=d, slot='Morning', range_id='r1'))
            db.session.commit()

            unchanged = db.session.get(Booking, booking_id)
            assert unchanged.status == 'confirmed'
            assert unchanged.walker_id == w.id

    def test_frozen_slot_still_waitlists_when_full(self, app):
        """Freeze changes the auto-confirm outcome only — capacity/waitlist
        behavior is unaffected. One walker at capacity 1, already fully
        booked by another dog, so the new booking is full-not-empty
        (available=False, can_waitlist=True) rather than hard-rejected."""
        d = tomorrow()
        with app.app_context():
            make_walker_with_schedule(f'w_full_{id(self)}@test.com', d.weekday(), 'Morning')
            service = make_service(capacity=1)
            other_user = make_user(f'cl_other_{id(self)}@test.com')
            make_client_profile(other_user.id)
            other_dog = make_dog(name='Rex')
            attach_dog(other_dog.id, other_user.id)
            db.session.add(Booking(
                user_id=other_user.id, dog_id=other_dog.id, service_type_id=service.id,
                date=d, slot='Morning', status='confirmed',
            ))

            user = make_user(f'cl_full_{id(self)}@test.com')
            make_client_profile(user.id)
            dog = make_dog()
            attach_dog(dog.id, user.id)
            db.session.add(SlotFreeze(date=d, slot='Morning', range_id='r1'))
            db.session.commit()

            booking, auto_confirmed = create_booking(
                dog=dog, user_id=user.id, date=d, slot='Morning',
                service=service, actor_id=user.id, batch_id='b1',
            )
            db.session.commit()

            assert auto_confirmed is False
            assert booking.status == 'waitlisted'


class TestAdminFreezeCRUD:

    def _setup_admin(self, app):
        with app.app_context():
            admin = make_user(f'freeze_admin_{id(self)}@test.com', role='walker', is_admin=True)
            db.session.commit()
            return admin.email

    def test_add_single_day_freeze(self, app, client):
        email = self._setup_admin(app)
        login(client, email)
        d = tomorrow()
        resp = client.post('/admin/freezes', data=json.dumps({
            'date': d.isoformat(), 'slot': 'Morning', 'reason': 'Short-staffed',
        }), content_type='application/json')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['success'] is True
        assert data['created'] == 1

        with app.app_context():
            rows = SlotFreeze.query.filter_by(date=d, slot='Morning').all()
            assert len(rows) == 1
            assert rows[0].reason == 'Short-staffed'

            log = ActivityLog.query.filter_by(entity_type='slot_freeze', action='created').first()
            assert log is not None
            assert 'Morning' in log.summary

    def test_add_both_creates_two_rows_sharing_range_id(self, app, client):
        email = self._setup_admin(app)
        login(client, email)
        d = tomorrow()
        resp = client.post('/admin/freezes', data=json.dumps({
            'date': d.isoformat(), 'slot': 'Both',
        }), content_type='application/json')
        data = resp.get_json()
        assert data['success'] is True
        assert data['created'] == 2

        with app.app_context():
            rows = SlotFreeze.query.filter_by(date=d).all()
            assert {r.slot for r in rows} == {'Morning', 'Afternoon'}
            assert rows[0].range_id == rows[1].range_id

    def test_add_skips_already_frozen_slot(self, app, client):
        email = self._setup_admin(app)
        login(client, email)
        d = tomorrow()
        client.post('/admin/freezes', data=json.dumps({
            'date': d.isoformat(), 'slot': 'Morning',
        }), content_type='application/json')

        # Re-request the same date with 'Both' — Morning already frozen,
        # only Afternoon should be newly created.
        resp = client.post('/admin/freezes', data=json.dumps({
            'date': d.isoformat(), 'slot': 'Both',
        }), content_type='application/json')
        data = resp.get_json()
        assert data['success'] is True
        assert data['created'] == 1
        assert d.isoformat() in data['skipped_dates']

        with app.app_context():
            rows = SlotFreeze.query.filter_by(date=d).all()
            assert len(rows) == 2

    def test_add_all_dates_already_frozen_rejected(self, app, client):
        email = self._setup_admin(app)
        login(client, email)
        d = tomorrow()
        client.post('/admin/freezes', data=json.dumps({
            'date': d.isoformat(), 'slot': 'Morning',
        }), content_type='application/json')

        resp = client.post('/admin/freezes', data=json.dumps({
            'date': d.isoformat(), 'slot': 'Morning',
        }), content_type='application/json')
        data = resp.get_json()
        assert resp.status_code == 400
        assert data['success'] is False

    def test_delete_freeze_range_removes_all_rows_and_logs(self, app, client):
        email = self._setup_admin(app)
        login(client, email)
        d = tomorrow()
        client.post('/admin/freezes', data=json.dumps({
            'date': d.isoformat(), 'slot': 'Both',
        }), content_type='application/json')

        with app.app_context():
            range_id = SlotFreeze.query.filter_by(date=d).first().range_id

        resp = client.delete(f'/admin/freezes/range/{range_id}')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['success'] is True

        with app.app_context():
            assert SlotFreeze.query.filter_by(range_id=range_id).count() == 0
            log = ActivityLog.query.filter_by(entity_type='slot_freeze', action='removed').first()
            assert log is not None

    def test_delete_unknown_range_404s(self, app, client):
        email = self._setup_admin(app)
        login(client, email)
        resp = client.delete('/admin/freezes/range/doesnotexist')
        assert resp.status_code == 404

    def test_non_admin_cannot_add_freeze(self, app, client):
        with app.app_context():
            user = make_user(f'freeze_client_{id(self)}@test.com', role='client')
            make_client_profile(user.id)
            db.session.commit()
            email = user.email
        login(client, email)
        resp = client.post('/admin/freezes', data=json.dumps({
            'date': tomorrow().isoformat(), 'slot': 'Morning',
        }), content_type='application/json')
        assert resp.status_code in (302, 403)


class TestSlotAvailabilityFrozenKey:

    def test_frozen_key_reflects_slot_freeze_state(self, app, client):
        d = tomorrow()
        with app.app_context():
            make_service()
            user = make_user(f'cl_avail_{id(self)}@test.com')
            make_client_profile(user.id)
            db.session.add(SlotFreeze(date=d, slot='Morning', range_id='r1'))
            db.session.commit()
            email = user.email

        login(client, email)
        resp = client.get(f'/api/slot_availability?date={d.isoformat()}&service=walk')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['Morning']['frozen'] is True
        assert data['Afternoon']['frozen'] is False
