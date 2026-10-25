"""Who hears about a booking action — app/utils/booking_notify.py.

Exact recipient sets per route: the household (every current owner of the
dog, minus the actor) mirrors one notification; admins hear about
client-initiated actions only; walkers are never notified (their one push is
the 10:00 daily summary); the actor never hears about their own action; one
notification per person. Plus #13: a recurring series that fails part-way
still notifies for what it saved.
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from app import db as _db
from app.models import (
    Booking, DogOwner, Notification, ServiceType, Walker, WalkerAdHocAvailability,
    WalkerSchedule,
)
from app.services import booking_service
from app.utils.booking_notify import notify_created
from tests.conftest import login, make_user

DAY = date(2099, 6, 2)                       # a Tuesday
TITLE_DAY = 'Tue 2 Jun'


@pytest.fixture
def co_owner(dog):
    co = make_user(firstname='Casey', lastname='Coowner', email='casey@test.dogboxx.org')
    _db.session.add(DogOwner(dog_id=dog.id, user_id=co.id, role='secondary'))
    _db.session.commit()
    return co


def _schedule(user, slots=('Morning',), days=range(5)):
    walker = Walker.query.filter_by(user_id=user.id).first()
    if walker is None:
        walker = Walker(user_id=user.id)
        _db.session.add(walker)
        _db.session.flush()
    for d in days:
        for s in slots:
            _db.session.add(WalkerSchedule(walker_id=walker.id, day_of_week=d,
                                           slot=s, active=True))
    _db.session.commit()
    return walker


def _notifs(user):
    return [(n.notification_type, n.title, n.body) for n in
            Notification.query.filter_by(recipient_id=user.id).order_by(Notification.id)]


class TestClientBooking:
    def test_auto_confirmed(self, app, client, client_user, dog, co_owner,
                            admin_user, walker_user, service_type):
        _schedule(walker_user)
        login(client, client_user.email)
        resp = client.post('/book', json={'date': DAY.isoformat(), 'slot': 'Morning',
                                          'dog_id': dog.id})
        assert resp.get_json()['status'] == 'confirmed'

        title = f"Client booked Buddy's morning walk on {TITLE_DAY}"
        assert _notifs(client_user) == []
        assert _notifs(co_owner) == [('booking_confirmed', title, None)]
        assert _notifs(admin_user) == [('booking_confirmed', title, 'Booked with Walker.')]
        assert _notifs(walker_user) == []

    def test_drop_in_request(self, app, client, client_user, dog, co_owner, admin_user,
                             walker_user):
        # Drop-ins are never auto-assigned, so this lands as a request.
        _db.session.add(ServiceType(
            name='Drop In', slug='drop-in', capacity_model='walker_assigned',
            slot_type='morning_afternoon', requires_walker=True,
            default_max_capacity=6, active=True))
        _schedule(walker_user).does_drop_ins = True
        _db.session.commit()
        login(client, client_user.email)
        resp = client.post('/book_drop_in', json={'date': DAY.isoformat(), 'slot': 'Morning',
                                                  'dog_id': dog.id})
        assert resp.get_json()['status'] == 'requested'

        title = f"Client requested Buddy's morning drop-in on {TITLE_DAY}"
        assert _notifs(client_user) == []
        assert [n[:2] for n in _notifs(co_owner)] == [('booking_requested', title)]
        assert [n[:2] for n in _notifs(admin_user)] == [('booking_requested', title)]
        assert Notification.query.filter_by(recipient_id=admin_user.id).one().link == \
            '/admin/drop-in-board'
        assert _notifs(walker_user) == []

    def test_same_day_reaches_admins_as_urgent(self, app, client, client_user, dog,
                                                co_owner, admin_user, service_type):
        today = Booking.query.session.execute(_db.select(_db.func.current_date())).scalar()
        login(client, client_user.email)
        with patch('app.blueprints.client.views.bookings._is_same_day', return_value=True):
            resp = client.post('/book', json={'date': today.isoformat(), 'slot': 'Morning',
                                              'dog_id': dog.id})
        assert resp.get_json()['success'] is True

        assert _notifs(client_user) == []
        assert [n[0] for n in _notifs(co_owner)] == ['booking_requested']
        admin = _notifs(admin_user)
        assert [n[0] for n in admin] == ['same_day_request']
        assert admin[0][1].startswith("Same-day request — Client requested Buddy's morning walk")

    def test_admin_who_is_the_walker_gets_one_notification(
            self, app, client, client_user, dog, admin_user, service_type):
        _schedule(admin_user)
        login(client, client_user.email)
        client.post('/book', json={'date': DAY.isoformat(), 'slot': 'Morning', 'dog_id': dog.id})
        assert [n[0] for n in _notifs(admin_user)] == ['booking_confirmed']

    def test_recurring_household_grouped(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        _schedule(walker_user)
        login(client, client_user.email)
        resp = client.post('/recurring_booking', json={
            'start_date': DAY.isoformat(), 'end_date': (DAY + timedelta(days=7)).isoformat(),
            'slot': 'Morning', 'frequency': 'daily', 'dog_id': dog.id})
        assert resp.get_json()['confirmed'] == 6          # Tue 2 – Tue 9 Jun, weekdays

        assert _notifs(client_user) == []
        assert _notifs(co_owner) == [
            ('booking_confirmed', "Client booked Buddy's 6 walks (Tue 2 – Tue 9 Jun)",
             '6 walks booked.')]
        assert len(_notifs(admin_user)) == 1
        assert _notifs(walker_user) == []


class TestAdminBooking:
    def test_whole_household_hears_other_admins_dont(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        other_admin = make_user(firstname='Other', email='other-admin@test.dogboxx.org',
                                role='walker', is_admin=True)
        _schedule(walker_user)
        login(client, admin_user.email)
        resp = client.post('/admin/book_for_dog', json={
            'dog_id': dog.id, 'user_id': client_user.id,
            'date': DAY.isoformat(), 'slot': 'Morning'})
        assert resp.get_json()['success'] is True

        expected = [('booking_confirmed', f"Admin booked Buddy's morning walk on {TITLE_DAY}", None)]
        assert _notifs(client_user) == expected
        assert _notifs(co_owner) == expected
        assert _notifs(admin_user) == []
        assert _notifs(other_admin) == []
        assert _notifs(walker_user) == []


class TestHouseholdIsCurrentOwners:
    def test_booker_who_no_longer_owns_the_dog_hears_nothing(
            self, app, client_user, dog, admin_user, service_type):
        former = make_user(firstname='Former', email='former@test.dogboxx.org')
        b = Booking(user_id=former.id, dog_id=dog.id, service_type_id=service_type.id,
                    date=DAY, slot='Morning', status='requested')
        _db.session.add(b)
        _db.session.flush()
        notify_created([b], actor=admin_user, by_admin=True)
        _db.session.commit()
        assert _notifs(former) == []
        assert len(_notifs(client_user)) == 1


def _fail_on_call(n):
    """create_booking that works for the first n-1 calls, then raises."""
    real = booking_service.create_booking
    calls = {'n': 0}

    def wrapper(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] >= n:
            raise RuntimeError('simulated DB failure')
        return real(*args, **kwargs)
    return wrapper


class TestRecurringPartialFailure:
    """#13: bookings saved before a mid-series failure still get notified."""

    def test_client_series(self, app, client, client_user, dog, co_owner, admin_user,
                           walker_user, service_type):
        _schedule(walker_user)
        login(client, client_user.email)
        with patch('app.blueprints.client.views.bookings.create_booking', _fail_on_call(3)):
            resp = client.post('/recurring_booking', json={
                'start_date': DAY.isoformat(), 'end_date': (DAY + timedelta(days=7)).isoformat(),
                'slot': 'Morning', 'frequency': 'daily', 'dog_id': dog.id})
        assert resp.status_code == 500
        assert resp.get_json()['message'] == "Some walks couldn't be booked — please try again."

        assert Booking.query.filter_by(dog_id=dog.id).count() == 2
        assert [n[1] for n in _notifs(co_owner)] == [
            "Client booked Buddy's 2 walks (Tue 2 – Wed 3 Jun)"]
        assert len(_notifs(admin_user)) == 1

    def test_admin_series(self, app, client, client_user, dog, co_owner, admin_user,
                          walker_user, service_type):
        _schedule(walker_user)
        login(client, admin_user.email)
        with patch('app.blueprints.admin.views.dogs.create_booking', _fail_on_call(3)):
            resp = client.post('/admin/recurring_for_dog', json={
                'dog_id': dog.id, 'user_id': client_user.id,
                'start_date': DAY.isoformat(), 'end_date': (DAY + timedelta(days=7)).isoformat(),
                'day_slots': [{'day': d, 'slot': 'Morning'} for d in range(5)]})
        assert resp.status_code == 500

        assert Booking.query.filter_by(dog_id=dog.id).count() == 2
        expected = ["Admin booked Buddy's 2 walks (Tue 2 – Wed 3 Jun)"]
        assert [n[1] for n in _notifs(client_user)] == expected
        assert [n[1] for n in _notifs(co_owner)] == expected


# ── Board actions, cancellations, closures (redesign PR 2) ──────────────────

def _second_walker():
    u = make_user(firstname='Bea', lastname='Walker', email='bea@test.dogboxx.org', role='walker')
    return u, _schedule(u, slots=('Morning', 'Afternoon'))


def _walk(owner, dog, service_type, *, status='confirmed', walker=None,
          confirmed=True, slot='Morning'):
    b = Booking(user_id=owner.id, dog_id=dog.id, service_type_id=service_type.id,
                date=DAY, slot=slot, status=status,
                walker_id=walker.id if walker else None,
                confirmed_at=datetime(2099, 5, 1) if confirmed else None)
    _db.session.add(b)
    _db.session.commit()
    return b


def _assign(flask_client, booking, walker, **extra):
    return flask_client.post('/admin/assign_walker', json={
        'booking_id': booking.id, 'walker_id': walker.id if walker else None, **extra})


class TestBoard:
    def test_first_confirmation_tells_household(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, status='requested', confirmed=False)
        login(client, admin_user.email)
        assert _assign(client, b, walker).get_json()['success'] is True

        expected = [('booking_confirmed', f"Buddy's morning walk on {TITLE_DAY} confirmed", None)]
        assert _notifs(client_user) == expected
        assert _notifs(co_owner) == expected
        assert _notifs(walker_user) == []
        assert _notifs(admin_user) == []

    def test_reassign_tells_nobody(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        old = _schedule(walker_user)
        bea, new = _second_walker()
        b = _walk(client_user, dog, service_type, walker=old)
        login(client, admin_user.email)
        assert _assign(client, b, new).get_json()['success'] is True

        assert _notifs(client_user) == []
        assert _notifs(co_owner) == []
        assert _notifs(walker_user) == []
        assert _notifs(bea) == []

    def test_reconfirm_after_reset_tells_nobody(
            self, app, client, client_user, dog, co_owner, admin_user, service_type):
        bea, new = _second_walker()
        b = _walk(client_user, dog, service_type, status='requested')   # reset: confirmed before
        login(client, admin_user.email)
        assert _assign(client, b, new).get_json()['success'] is True

        assert _notifs(client_user) == []
        assert _notifs(co_owner) == []
        assert _notifs(bea) == []

    def test_unassign_tells_nobody(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, walker=walker)
        login(client, admin_user.email)
        assert _assign(client, b, None).get_json()['success'] is True

        assert _notifs(client_user) == []
        assert _notifs(co_owner) == []
        assert _notifs(walker_user) == []

    def test_slot_move_tells_household_only(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, walker=walker)
        login(client, admin_user.email)
        resp = _assign(client, b, walker, slot='Afternoon', slot_override=True)
        assert resp.get_json()['success'] is True

        expected = [('system', f"Buddy's walk on {TITLE_DAY} has moved to the afternoon",
                     'Originally booked for morning.')]
        assert _notifs(client_user) == expected
        assert _notifs(co_owner) == expected
        assert _notifs(walker_user) == []

    def test_pending_walk_confirmed_and_moved_is_one_message(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, status='requested', confirmed=False)
        login(client, admin_user.email)
        resp = _assign(client, b, walker, slot='Afternoon', slot_override=True)
        assert resp.get_json()['success'] is True

        assert _notifs(co_owner) == [
            ('booking_confirmed', f"Buddy's walk on {TITLE_DAY} is confirmed for the afternoon",
             'Originally booked for morning.')]

    def test_admin_who_is_the_old_walker_hears_nothing(
            self, app, client, client_user, dog, admin_user, service_type):
        old = _schedule(admin_user)
        bea, new = _second_walker()
        b = _walk(client_user, dog, service_type, walker=old)
        login(client, admin_user.email)
        _assign(client, b, new)
        assert _notifs(admin_user) == []

    def test_decline_tells_whole_household(
            self, app, client, client_user, dog, co_owner, admin_user, service_type):
        b = _walk(client_user, dog, service_type, status='requested', confirmed=False)
        login(client, admin_user.email)
        assert client.post(f'/admin/booking/{b.id}/decline').get_json()['success'] is True

        expected = [('booking_cancelled', f"Buddy's morning walk on {TITLE_DAY} has been declined",
                     "Please contact Lydia if this is unexpected!")]
        assert _notifs(client_user) == expected
        assert _notifs(co_owner) == expected


class TestCancel:
    def test_co_owner_cancels(self, app, client, client_user, dog, co_owner, admin_user,
                              walker_user, service_type):
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, walker=walker)
        login(client, co_owner.email)
        assert client.post('/cancel_booking', data={'booking_id': b.id}).get_json()['success']

        title = f"Casey cancelled Buddy's morning walk on {TITLE_DAY}"
        assert _notifs(co_owner) == []
        assert [n[1] for n in _notifs(client_user)] == [title]
        assert [n[1] for n in _notifs(admin_user)] == [title]
        assert _notifs(walker_user) == []

    def test_admin_cancel_tells_whole_household(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        other_admin = make_user(firstname='Other', email='other-admin@test.dogboxx.org',
                                role='walker', is_admin=True)
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, walker=walker)
        login(client, admin_user.email)
        assert client.post('/cancel_booking', data={'booking_id': b.id}).get_json()['success']

        expected = [('booking_cancelled', f"Buddy's morning walk on {TITLE_DAY} cancelled",
                     "Please contact Lydia if this is unexpected!")]
        assert _notifs(client_user) == expected
        assert _notifs(co_owner) == expected
        assert _notifs(walker_user) == []
        assert _notifs(other_admin) == []

    def test_closure_tells_household_with_reason(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        walker = _schedule(walker_user)
        _walk(client_user, dog, service_type, walker=walker)
        login(client, admin_user.email)
        client.post('/admin/closures', json={'date': DAY.isoformat(), 'reason': 'Storm'})

        for user in (client_user, co_owner):
            notifs = _notifs(user)
            assert len(notifs) == 1
            assert notifs[0][0] == 'booking_cancelled'
            assert 'Storm' in notifs[0][2]
        assert _notifs(walker_user) == []


# ── Walker availability (redesign PR 3) ─────────────────────────────────────

class TestWalkerAvailability:
    """Resets are silent to the household and the walker; admins hear only when
    a walker's own change leaves walks to reassign."""

    def test_walker_time_off_with_walks_tells_admins_only(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, walker=walker)
        login(client, walker_user.email)
        resp = client.post('/walker/unavailability', json={'date': DAY.isoformat(),
                                                             'slot': 'Morning'})
        assert resp.status_code == 201

        assert (_db.session.get(Booking, b.id).status) == 'requested'
        assert _notifs(client_user) == []
        assert _notifs(co_owner) == []
        assert _notifs(walker_user) == []
        assert _notifs(admin_user) == [
            ('walker_availability', f"Walker is unavailable — Morning on {TITLE_DAY}",
             '1 booking needs reassigning')]

    def test_walker_time_off_without_walks_tells_nobody(
            self, app, client, admin_user, walker_user, service_type):
        _schedule(walker_user)
        login(client, walker_user.email)
        client.post('/walker/unavailability', json={'date': DAY.isoformat(), 'slot': 'Morning'})
        assert _notifs(admin_user) == []

    def test_walker_adding_a_day_tells_nobody(self, app, client, admin_user, walker_user):
        _schedule(walker_user)                         # Morning only
        login(client, walker_user.email)
        resp = client.post('/walker/adhoc', json={'date': DAY.isoformat(), 'slot': 'Afternoon'})
        assert resp.status_code == 201
        assert _notifs(admin_user) == []

    def test_walker_removing_a_day_with_walks_tells_admins(
            self, app, client, client_user, dog, admin_user, walker_user, service_type):
        walker = _schedule(walker_user)
        adhoc = WalkerAdHocAvailability(walker_id=walker.id, date=DAY, slot='Afternoon')
        _db.session.add(adhoc)
        _db.session.commit()
        _walk(client_user, dog, service_type, walker=walker, slot='Afternoon')
        login(client, walker_user.email)
        assert client.delete(f'/walker/adhoc/{adhoc.id}').get_json()['success'] is True

        assert _notifs(client_user) == []
        assert _notifs(admin_user) == [
            ('walker_availability',
             f"Walker removed their Afternoon availability — {TITLE_DAY}",
             '1 booking needs reassigning')]

    def test_admin_marking_walker_off_tells_nobody(
            self, app, client, client_user, dog, co_owner, admin_user, walker_user,
            service_type):
        walker = _schedule(walker_user)
        _walk(client_user, dog, service_type, walker=walker)
        login(client, admin_user.email)
        resp = client.post(f'/admin/walkers/{walker.id}/unavailability',
                           json={'date': DAY.isoformat(), 'slot': 'Morning'})
        assert resp.get_json()['success'] is True

        assert _notifs(client_user) == []
        assert _notifs(co_owner) == []
        assert _notifs(admin_user) == []
        assert _notifs(walker_user) == []

    def test_admin_walker_marking_herself_off_tells_nobody(
            self, app, client, client_user, dog, admin_user, service_type):
        other_admin = make_user(firstname='Other', email='other-admin@test.dogboxx.org',
                                role='walker', is_admin=True)
        walker = _schedule(admin_user)
        _walk(client_user, dog, service_type, walker=walker)
        login(client, admin_user.email)
        client.post('/walker/unavailability', json={'date': DAY.isoformat(), 'slot': 'Morning'})

        assert _notifs(other_admin) == []
        assert _notifs(client_user) == []

    def test_deactivating_a_walker_tells_nobody(
            self, app, client, client_user, dog, admin_user, walker_user, service_type):
        walker = _schedule(walker_user)
        b = _walk(client_user, dog, service_type, walker=walker)
        login(client, admin_user.email)
        assert client.post(f'/admin/walkers/{walker_user.id}/deactivate').get_json()['success'] is True

        assert _db.session.get(Booking, b.id).status == 'requested'
        assert _notifs(client_user) == []
        assert _notifs(walker_user) == []
