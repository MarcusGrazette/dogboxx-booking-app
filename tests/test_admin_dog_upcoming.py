"""
Tests for /admin/dogs/<dog_id>/upcoming-bookings — the optional service-type
filter (slug, e.g. 'group-walk' / 'drop-in'). Missing/empty = no filter.

Like test_admin_bulk_cancel, we commit the whole graph in one transaction so
the flushed-but-uncommitted conftest fixtures persist for the HTTP request's
own connection on Postgres.
"""
import datetime

from app import db
from app.models import Booking, ServiceType


def _drop_in_service():
    st = ServiceType(
        name='Drop In',
        slug='drop-in',
        capacity_model='walker_assigned',
        slot_type='morning_afternoon',
        requires_walker=False,
        default_max_capacity=6,
        active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


def _booking(user, dog, service_type, date, slot='Morning'):
    b = Booking(
        user_id=user.id,
        dog_id=dog.id,
        service_type_id=service_type.id,
        date=date,
        slot=slot,
        status='confirmed',
    )
    db.session.add(b)
    db.session.flush()
    return b


class TestUpcomingServiceFilter:

    def _seed_one_each(self, client_user, dog, service_type):
        """One walk + one drop-in on different dates (distinct slots/dates keep
        the active (dog,date,slot) unique index happy)."""
        drop_in = _drop_in_service()
        d1 = datetime.date.today() + datetime.timedelta(days=1)
        d2 = datetime.date.today() + datetime.timedelta(days=2)
        _booking(client_user, dog, service_type, d1)   # walk
        _booking(client_user, dog, drop_in, d2)        # drop-in
        db.session.commit()

    def test_no_filter_returns_both(self, client_user, dog, service_type, logged_in_admin):
        self._seed_one_each(client_user, dog, service_type)
        resp = logged_in_admin.get(f'/admin/dogs/{dog.id}/upcoming-bookings')
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['total'] == 2

    def test_filter_walk_only(self, client_user, dog, service_type, logged_in_admin):
        self._seed_one_each(client_user, dog, service_type)
        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/upcoming-bookings?service=group-walk'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['total'] == 1
        assert data['bookings'][0]['service_slug'] == 'group-walk'

    def test_filter_drop_in_only(self, client_user, dog, service_type, logged_in_admin):
        self._seed_one_each(client_user, dog, service_type)
        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/upcoming-bookings?service=drop-in'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['total'] == 1
        assert data['bookings'][0]['service_slug'] == 'drop-in'

    def test_unknown_service_slug_ignored(self, client_user, dog, service_type, logged_in_admin):
        """A bogus slug matches no ServiceType → filter is silently dropped."""
        self._seed_one_each(client_user, dog, service_type)
        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/upcoming-bookings?service=nonsense'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['total'] == 2
