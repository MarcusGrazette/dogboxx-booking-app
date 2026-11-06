"""Database-error replies at the two routes that used db_error_handler.py
(FEATURES #90, which replaced it with plain try/except):

- POST /admin/assign_walker (board): any error mid-assign rolls back and
  returns a JSON 500 "Server error".
- POST / (the client home page's no-JS booking form): a database error rolls
  back, flashes a message and redirects home.
"""
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError, OperationalError

from app import db
from app.blueprints.admin.views import board as board_module
from app.blueprints.client.views import bookings as bookings_module
from app.models import Booking, WalkerSchedule
from tests import test_admin_assign_walker as aw
from tests import test_bookings as tb


def _db_error(cls):
    return cls('INSERT …', {}, Exception('simulated'))


class TestAssignWalkerDbError:

    def test_db_error_mid_assign_rolls_back_and_returns_json_500(self, app, client):
        monday = aw._next_weekday(0)
        with app.app_context():
            admin = aw._make_admin()
            _, walker = aw._make_walker()
            db.session.add(WalkerSchedule(walker_id=walker.id, day_of_week=0,
                                          slot='Morning', active=True))
            booking = aw._make_booking(monday, slot='Morning')
            db.session.commit()
            admin_email, walker_id, booking_id = admin.email, walker.id, booking.id

        aw._login(client, admin_email)
        with patch.object(board_module, 'notify_assigned',
                          side_effect=_db_error(OperationalError)):
            resp = aw._post_assign(client, booking_id, walker_id, slot='Morning')

        assert resp.status_code == 500
        assert resp.get_json() == {'success': False, 'message': 'Server error'}
        with app.app_context():
            booking = db.session.get(Booking, booking_id)
            assert booking.status == 'requested' and booking.walker_id is None


class TestBookingFormDbError:

    EMAIL = 'form_db_error@test.com'

    def _setup(self, app):
        with app.app_context():
            tb.make_walker_with_schedule('form_db_walker@test.com',
                                         tb.tomorrow().weekday(), 'Morning')
            tb.make_service(capacity=6)
            user = tb.make_user(self.EMAIL)
            tb.make_client_profile(user.id)
            dog = tb.make_dog()
            tb.attach_dog(dog.id, user.id)
            db.session.commit()
        return {'date': tb.tomorrow().isoformat(), 'slot': 'Morning'}

    def _post(self, app, client, error, **kw):
        form = self._setup(app)
        tb.login(client, self.EMAIL)
        with patch.object(bookings_module, 'create_booking', side_effect=error):
            return client.post('/', data=form, **kw)

    def test_integrity_error_flashes_conflict_and_saves_nothing(self, app, client):
        resp = self._post(app, client, _db_error(IntegrityError), follow_redirects=True)
        assert resp.status_code == 200
        assert b'Could not create booking due to a conflict' in resp.data
        with app.app_context():
            assert Booking.query.count() == 0

    def test_operational_error_flashes_unavailable(self, app, client):
        resp = self._post(app, client, _db_error(OperationalError), follow_redirects=True)
        assert b'Our booking system is temporarily unavailable' in resp.data

    def test_unexpected_error_flashes_generic_message(self, app, client):
        resp = self._post(app, client, RuntimeError('boom'), follow_redirects=True)
        assert b'An unexpected error occurred. Please try again later.' in resp.data

    def test_error_redirects_home_rather_than_rendering(self, app, client):
        """Post/redirect/get: a refresh after an error mustn't resubmit the form."""
        resp = self._post(app, client, _db_error(IntegrityError))
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/')
