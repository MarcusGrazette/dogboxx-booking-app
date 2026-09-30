"""Walker eligibility invariants (develop review #6).

Every path that takes a walker out of the pool must reset their future
confirmed bookings, and a user who is no longer a walker must never be
picked or shown as one, whatever availability rows survive:

- toggle_walker_drop_ins (off) resets future confirmed drop-ins only
- remove_walker_role deletes future ad-hoc days (past ones stay)
- deactivate_client on a dual-role walker resets their future walks
- capacity.get_available_walkers / the board skip a demoted walker
- the old HTML schedule form route is gone (only the modal route remains)
"""
import datetime

from app import db
from app.capacity import auto_assign_walker, get_available_walkers
from app.models import (
    Booking, BookingStatusChange, Client, Dog, DogOwner, Notification,
    ServiceType, Walker, WalkerAdHocAvailability, WalkerSchedule,
)
from app.utils.dates import local_today
from tests.conftest import login, make_user


def _weekday(offset_days):
    d = local_today() + datetime.timedelta(days=offset_days)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1 if offset_days >= 0 else -1)
    return d


FUTURE = _weekday(7)
PAST = _weekday(-7)


def _service(slug):
    st = ServiceType(
        name='Drop In' if slug == ServiceType.DROP_IN else 'Group Walk', slug=slug,
        capacity_model='walker_assigned', slot_type='morning_afternoon',
        requires_walker=True, default_max_capacity=6, active=True,
    )
    db.session.add(st)
    db.session.commit()
    return st


def _walker(firstname='Sam', *, does_drop_ins=False, with_client=False, schedule=()):
    user = make_user(firstname=firstname, lastname='Walker', role='walker')
    walker = Walker(user_id=user.id, does_drop_ins=does_drop_ins)
    db.session.add(walker)
    if with_client:
        db.session.add(Client(user_id=user.id, onboarding_completed=True))
    db.session.flush()
    for day, slot in schedule:
        db.session.add(WalkerSchedule(walker_id=walker.id, day_of_week=day, slot=slot, active=True))
    db.session.commit()
    return user, walker


def _owner_and_dog():
    owner = make_user(firstname='Jane', lastname='Smith', role='client')
    db.session.add(Client(user_id=owner.id, onboarding_completed=True))
    dog = Dog(name='Rex', breed='Lab')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=owner.id, role='primary'))
    db.session.commit()
    return owner, dog


def _booking(owner, dog, service, walker, date, slot='Morning', status='confirmed'):
    b = Booking(user_id=owner.id, dog_id=dog.id, service_type_id=service.id,
                walker_id=walker.id, date=date, slot=slot, status=status)
    db.session.add(b)
    db.session.commit()
    return b.id


def _admin_client(client):
    admin = make_user(firstname='Admin', lastname='User', role='walker', is_admin=True)
    login(client, admin.email)
    return admin


def _assert_reset(booking_id, reason_fragment):
    b = db.session.get(Booking, booking_id)
    assert b.status == 'requested'
    assert b.walker_id is None
    bsc = (BookingStatusChange.query.filter_by(booking_id=booking_id)
           .order_by(BookingStatusChange.id.desc()).first())
    assert bsc.to_status == 'requested'
    assert reason_fragment in (bsc.notes or '')


def _assert_untouched(booking_id, walker_id):
    b = db.session.get(Booking, booking_id)
    assert b.status == 'confirmed'
    assert b.walker_id == walker_id


def _reset_notifications(user_id):
    # NotificationBatch stores the booking_reset kind as type 'system'
    # (notifications.py kind→type map); nothing else notifies these owners.
    return Notification.query.filter_by(recipient_id=user_id,
                                        notification_type='system').count()


class TestDropInToggleOff:

    def test_resets_future_confirmed_drop_ins_only(self, app, client):
        drop_in = _service(ServiceType.DROP_IN)
        walk = _service(ServiceType.WALK)
        _, walker = _walker(does_drop_ins=True)
        owner, dog = _owner_and_dog()
        future_drop = _booking(owner, dog, drop_in, walker, FUTURE)
        past_drop = _booking(owner, dog, drop_in, walker, PAST)
        future_walk = _booking(owner, dog, walk, walker, FUTURE, slot='Afternoon')
        _admin_client(client)

        resp = client.post(f'/admin/walkers/{walker.user_id}/toggle-drop-ins')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['does_drop_ins'] is False
        assert body['affected_count'] == 1

        _assert_reset(future_drop, 'stopped doing drop-in visits')
        _assert_untouched(past_drop, walker.id)
        _assert_untouched(future_walk, walker.id)
        assert _reset_notifications(owner.id) == 1

    def test_turning_drop_ins_on_resets_nothing(self, app, client):
        drop_in = _service(ServiceType.DROP_IN)
        _, walker = _walker(does_drop_ins=False)
        owner, dog = _owner_and_dog()
        # Legacy state: a confirmed drop-in on a walker with drop-ins off.
        booking_id = _booking(owner, dog, drop_in, walker, FUTURE)
        _admin_client(client)

        resp = client.post(f'/admin/walkers/{walker.user_id}/toggle-drop-ins')
        assert resp.get_json()['affected_count'] == 0
        _assert_untouched(booking_id, walker.id)


class TestRemoveWalkerRole:

    def test_deletes_future_adhoc_keeps_past_and_resets_bookings(self, app, client):
        walk = _service(ServiceType.WALK)
        user, walker = _walker(with_client=True)
        db.session.add_all([
            WalkerAdHocAvailability(walker_id=walker.id, date=FUTURE, slot='Morning'),
            WalkerAdHocAvailability(walker_id=walker.id, date=PAST, slot='Morning'),
        ])
        db.session.commit()
        owner, dog = _owner_and_dog()
        booking_id = _booking(owner, dog, walk, walker, FUTURE)
        _admin_client(client)

        resp = client.post(f'/admin/walkers/{user.id}/remove-walker-role')
        assert resp.status_code == 200, resp.get_json()

        remaining = WalkerAdHocAvailability.query.filter_by(walker_id=walker.id).all()
        assert [a.date for a in remaining] == [PAST]
        _assert_reset(booking_id, "walker role was removed")
        assert _reset_notifications(owner.id) == 1


class TestDeactivateDualRoleFromClients:

    def test_resets_future_walks_and_leaves_schedule(self, app, client):
        walk = _service(ServiceType.WALK)
        user, walker = _walker(with_client=True, schedule=[(FUTURE.weekday(), 'Morning')])
        owner, dog = _owner_and_dog()
        future_id = _booking(owner, dog, walk, walker, FUTURE)
        past_id = _booking(owner, dog, walk, walker, PAST)
        _admin_client(client)

        resp = client.post(f'/admin/clients/{user.id}/deactivate')
        assert resp.status_code == 200, resp.get_json()

        _assert_reset(future_id, 'was deactivated')
        _assert_untouched(past_id, walker.id)
        assert _reset_notifications(owner.id) == 1
        assert WalkerSchedule.query.filter_by(walker_id=walker.id, active=True).count() == 1

    def test_plain_client_deactivation_unaffected(self, app, client):
        owner, _ = _owner_and_dog()
        _admin_client(client)
        resp = client.post(f'/admin/clients/{owner.id}/deactivate')
        assert resp.status_code == 200, resp.get_json()


class TestDemotedWalkerNeverEligible:
    """Backstop: leftover availability rows must not put a role='client'
    user back in the pool, even if a cleanup path missed them."""

    def _demoted_with_adhoc(self):
        user, walker = _walker(does_drop_ins=True, with_client=True)
        db.session.add(WalkerAdHocAvailability(walker_id=walker.id, date=FUTURE, slot='Morning'))
        user.role = 'client'
        db.session.commit()
        return walker

    def test_not_available_and_not_auto_assigned(self, app):
        _service(ServiceType.WALK)
        _service(ServiceType.DROP_IN)
        self._demoted_with_adhoc()
        assert get_available_walkers(FUTURE, 'Morning') == []
        assert get_available_walkers(FUTURE, 'Morning', drop_in=True) == []
        assert auto_assign_walker(FUTURE, 'Morning') is None

    def test_not_a_board_column(self, app, client):
        _service(ServiceType.WALK)
        _service(ServiceType.DROP_IN)
        walker = self._demoted_with_adhoc()
        _admin_client(client)
        for url in (f'/admin/api/board-data/{FUTURE.isoformat()}',
                    f'/admin/api/drop-in-board-data/{FUTURE.isoformat()}'):
            data = client.get(url).get_json()
            assert data['success'], url
            assert walker.id not in {w['id'] for w in data['walkers']}, url

    def test_active_walker_still_eligible(self, app):
        _service(ServiceType.WALK)
        _, walker = _walker(schedule=[(FUTURE.weekday(), 'Morning')])
        assert [w.id for w in get_available_walkers(FUTURE, 'Morning')] == [walker.id]


def test_old_schedule_form_route_is_gone(app, client):
    _, walker = _walker()
    _admin_client(client)
    assert client.get(f'/admin/walkers/{walker.id}/schedule').status_code == 404
