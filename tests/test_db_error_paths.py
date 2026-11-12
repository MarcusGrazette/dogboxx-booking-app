"""Database-error reply at POST /admin/assign_walker (board), one of the
routes that used db_error_handler.py (FEATURES #90): any error mid-assign
rolls back and returns a JSON 500 "Server error". The other, the home page's
no-JS booking form, was retired in #92; /book's equivalent is covered by
tests/test_booking_request.py.
"""
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import db
from app.blueprints.admin.views import board as board_module
from app.models import Booking, WalkerSchedule
from tests import test_admin_assign_walker as aw


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
