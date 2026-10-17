"""
Admin walk notes — POST /admin/booking/<id>/note (board.py::update_booking_note_admin).

The admin edits the booking's own client_notes (one note shared with the
client), and each real change writes an ActivityLog row whose summary never
contains the note text. The board's card data carries the note so the board
can show and pre-fill it.
"""
import datetime

from app import db
from app.models import ActivityLog, Booking
from tests.conftest import login

NOTE_SECRET = 'Rex had surgery, lead only'


def _make_booking(dog, client_user, service_type, *, date=None, slot='Morning',
                  status='requested', note=None):
    b = Booking(user_id=client_user.id, dog_id=dog.id, service_type_id=service_type.id,
                date=date or datetime.date(2026, 10, 15), slot=slot, status=status,
                client_notes=note)
    db.session.add(b)
    db.session.commit()
    return b.id


def _post(flask_client, booking_id, note):
    return flask_client.post(f'/admin/booking/{booking_id}/note', json={'note': note})


def _logs():
    return ActivityLog.query.filter_by(entity_type='booking_note').order_by(ActivityLog.id).all()


class TestAdminBookingNote:

    def test_admin_adds_note_and_logs_without_text(self, app, logged_in_admin, admin_user,
                                                   dog, client_user, service_type):
        bid = _make_booking(dog, client_user, service_type)
        resp = _post(logged_in_admin, bid, f'  {NOTE_SECRET}  ')
        assert resp.status_code == 200
        assert resp.get_json() == {'success': True, 'note': NOTE_SECRET}
        assert db.session.get(Booking, bid).client_notes == NOTE_SECRET

        [log] = _logs()
        assert log.action == 'added'
        assert log.entity_id == bid
        assert log.actor_id == admin_user.id
        assert log.summary == "Added the note on Buddy's morning walk on Thu 15 Oct"
        assert 'surgery' not in log.summary
        assert log.changes is None

    def test_admin_edits_client_note(self, app, logged_in_admin, dog, client_user, service_type):
        bid = _make_booking(dog, client_user, service_type, note='Client wrote this')
        assert _post(logged_in_admin, bid, 'Admin rewrote this').status_code == 200
        assert db.session.get(Booking, bid).client_notes == 'Admin rewrote this'
        [log] = _logs()
        assert log.action == 'updated'
        assert log.summary.startswith('Edited the note on')

    def test_blank_note_clears_it(self, app, logged_in_admin, dog, client_user, service_type):
        bid = _make_booking(dog, client_user, service_type, note='Old note')
        resp = _post(logged_in_admin, bid, '   ')
        assert resp.get_json() == {'success': True, 'note': None}
        assert db.session.get(Booking, bid).client_notes is None
        [log] = _logs()
        assert log.action == 'removed'

    def test_unchanged_note_logs_nothing(self, app, logged_in_admin, dog, client_user, service_type):
        bid = _make_booking(dog, client_user, service_type, note='Same')
        assert _post(logged_in_admin, bid, 'Same').status_code == 200
        assert _post(logged_in_admin, bid, '').status_code == 200   # clear…
        assert _post(logged_in_admin, bid, '').status_code == 200   # …clearing again is a no-op
        assert [l.action for l in _logs()] == ['removed']

    def test_drop_in_summary_says_drop_in(self, app, logged_in_admin, dog, client_user, service_type):
        service_type.slug = 'drop-in'
        db.session.commit()
        bid = _make_booking(dog, client_user, service_type, slot='Afternoon')
        _post(logged_in_admin, bid, 'Key under the mat')
        [log] = _logs()
        assert log.summary == "Added the note on Buddy's afternoon drop-in on Thu 15 Oct"

    def test_too_long_note_rejected_before_any_write(self, app, logged_in_admin, dog,
                                                     client_user, service_type):
        bid = _make_booking(dog, client_user, service_type, note='Keep me')
        resp = _post(logged_in_admin, bid, 'x' * (Booking.NOTE_MAX_LENGTH + 1))
        assert resp.status_code == 400
        assert db.session.get(Booking, bid).client_notes == 'Keep me'
        assert _logs() == []

    def test_unknown_booking_404(self, app, logged_in_admin):
        assert _post(logged_in_admin, 999999, 'hi').status_code == 404

    def test_client_cannot_use_admin_route(self, app, logged_in_client, dog, client_user,
                                           service_type):
        bid = _make_booking(dog, client_user, service_type)
        resp = _post(logged_in_client, bid, 'sneaky')
        assert resp.status_code == 403
        assert db.session.get(Booking, bid).client_notes is None

    def test_walker_cannot_use_admin_route(self, app, logged_in_walker, dog, client_user,
                                           service_type):
        bid = _make_booking(dog, client_user, service_type)
        assert _post(logged_in_walker, bid, 'sneaky').status_code == 403

    def test_client_still_sees_and_edits_admin_note(self, app, logged_in_admin, dog,
                                                    client_user, service_type):
        """One shared note: the client's own route edits what the admin wrote."""
        bid = _make_booking(dog, client_user, service_type)
        _post(logged_in_admin, bid, 'From the admin')
        client_session = app.test_client()
        login(client_session, client_user.email)
        resp = client_session.post(f'/booking/{bid}/note', json={'note': 'Client changed it'})
        assert resp.status_code == 200
        assert db.session.get(Booking, bid).client_notes == 'Client changed it'


class TestBoardDataCarriesNote:

    def test_board_data_includes_client_notes(self, app, logged_in_admin, dog, client_user,
                                              service_type):
        _make_booking(dog, client_user, service_type, note='Bring the long lead')
        resp = logged_in_admin.get('/admin/api/board-data/2026-10-15')
        assert resp.status_code == 200
        [card] = resp.get_json()['pending']
        assert card['client_notes'] == 'Bring the long lead'


class TestActivityFeedShowsNoteEdit:

    def test_feed_lists_note_edit(self, app, logged_in_admin, dog, client_user, service_type):
        bid = _make_booking(dog, client_user, service_type)
        _post(logged_in_admin, bid, NOTE_SECRET)
        html = logged_in_admin.get('/admin/activity').get_data(as_text=True)
        assert "Added the note on Buddy&#39;s morning walk on Thu 15 Oct" in html
        assert 'surgery' not in html
