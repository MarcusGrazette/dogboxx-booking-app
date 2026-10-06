"""Bell notifications from cancel_booking.

- An admin who is also the assigned walker (the owner, a covering walker)
  gets one notification, not an admin copy plus a walker copy.
- Titles say "drop-in" for a drop-in, for every recipient.
- The client is named by first name, like every other bell title.
"""
from datetime import date

import pytest

from app import db as _db
from app.models import Booking, DogOwner, Notification, ServiceType, Walker
from tests.conftest import login, make_user


def _service(slug):
    st = ServiceType(
        name='Drop In' if slug == 'drop-in' else 'Group Walk', slug=slug,
        capacity_model='walker_assigned', slot_type='morning_afternoon',
        requires_walker=True, default_max_capacity=6, active=True,
    )
    _db.session.add(st)
    _db.session.commit()
    return st


def _walker_profile(user):
    w = Walker(user_id=user.id)
    _db.session.add(w)
    _db.session.commit()
    return w


def _booking(user, dog, service, walker):
    b = Booking(
        user_id=user.id, dog_id=dog.id, service_type_id=service.id,
        date=date(2099, 6, 2), slot='Morning', status='confirmed',
        walker_id=walker.id,
    )
    _db.session.add(b)
    _db.session.commit()
    return b


def _titles_for(user_id):
    return [n.title for n in Notification.query.filter_by(
        recipient_id=user_id, notification_type='booking_cancelled')]


def _cancel(client, booking):
    resp = client.post('/cancel_booking', data={'booking_id': booking.id})
    assert resp.get_json()['success'] is True


@pytest.fixture
def drop_in():
    return _service('drop-in')


class TestClientCancel:
    def test_admin_walker_gets_one_notification(
            self, app, client, client_user, dog, admin_user, drop_in):
        booking = _booking(client_user, dog, drop_in, _walker_profile(admin_user))
        login(client, client_user.email)
        _cancel(client, booking)
        assert _titles_for(admin_user.id) == [
            "Client cancelled Buddy's morning drop-in on Tue 2 Jun"]

    def test_plain_walker_and_admin_both_notified(
            self, app, client, client_user, dog, admin_user, walker_user, drop_in):
        walker = Walker.query.filter_by(user_id=walker_user.id).one()
        booking = _booking(client_user, dog, drop_in, walker)
        login(client, client_user.email)
        _cancel(client, booking)
        expected = ["Client cancelled Buddy's morning drop-in on Tue 2 Jun"]
        assert _titles_for(admin_user.id) == expected
        assert _titles_for(walker_user.id) == expected

    def test_co_owner_title(self, app, client, client_user, dog, admin_user, walker_user, drop_in):
        co = make_user(firstname='Casey', role='client')
        _db.session.add(DogOwner(dog_id=dog.id, user_id=co.id, role='secondary'))
        _db.session.commit()
        walker = Walker.query.filter_by(user_id=walker_user.id).one()
        booking = _booking(client_user, dog, drop_in, walker)
        login(client, client_user.email)
        _cancel(client, booking)
        assert _titles_for(co.id) == [
            "Client cancelled Buddy's morning drop-in on Tue 2 Jun"]

    def test_walk_still_says_walk(self, app, client, client_user, dog, admin_user, walker_user):
        walk = _service('group-walk')
        walker = Walker.query.filter_by(user_id=walker_user.id).one()
        booking = _booking(client_user, dog, walk, walker)
        login(client, client_user.email)
        _cancel(client, booking)
        expected = ["Client cancelled Buddy's morning walk on Tue 2 Jun"]
        assert _titles_for(admin_user.id) == expected
        assert _titles_for(walker_user.id) == expected


class TestAdminCancel:
    def test_walker_title_says_drop_in(
            self, app, client, client_user, dog, admin_user, walker_user, drop_in):
        walker = Walker.query.filter_by(user_id=walker_user.id).one()
        booking = _booking(client_user, dog, drop_in, walker)
        login(client, admin_user.email)
        _cancel(client, booking)
        assert _titles_for(walker_user.id) == [
            "Buddy's morning drop-in on Tue 2 Jun was cancelled"]
        assert _titles_for(client_user.id) == [
            "Buddy's morning drop-in on Tue 2 Jun has been cancelled"]
