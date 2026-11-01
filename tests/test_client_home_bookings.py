"""Client home bookings: upcoming only on the page, past days on demand
(develop review #21).

The home page used to render every booking the client ever had (past ones
hidden) so the calendar filter could show them; it now carries today onwards
and the filter fetches a past day from /day_bookings/<date>.
"""
import datetime

from app import db
from app.models import Booking, Dog, DogOwner
from app.utils.dates import local_today
from tests.conftest import login, make_user


def _book(dog, user, service, date, slot='Morning', status='confirmed', note=None):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=service.id,
                date=date, slot=slot, status=status, client_notes=note)
    db.session.add(b)
    db.session.commit()
    return b


def _days(n):
    return local_today() + datetime.timedelta(days=n)


class TestHomePage:

    def test_renders_upcoming_bookings_only(self, logged_in_client, client_user, dog, service_type):
        _book(dog, client_user, service_type, _days(-30))
        _book(dog, client_user, service_type, _days(0))
        _book(dog, client_user, service_type, _days(3))
        html = logged_in_client.get('/').get_data(as_text=True)
        assert f'data-date="{_days(-30).isoformat()}"' not in html
        assert f'data-date="{_days(0).isoformat()}"' in html
        assert f'data-date="{_days(3).isoformat()}"' in html

    def test_same_day_morning_renders_before_afternoon(self, logged_in_client, client_user,
                                                       dog, service_type):
        _book(dog, client_user, service_type, _days(2), 'Afternoon')
        _book(dog, client_user, service_type, _days(2), 'Morning')
        html = logged_in_client.get('/').get_data(as_text=True)
        assert html.index('data-slot="Morning"') < html.index('data-slot="Afternoon"')

    def test_pager_is_rendered_hidden_at_ten_or_fewer(self, logged_in_client, client_user,
                                                      dog, service_type):
        for i in range(1, 11):
            _book(dog, client_user, service_type, _days(i))
        html = logged_in_client.get('/').get_data(as_text=True)
        # Always present so booking an 11th can reveal it.
        assert f'id="walk-pager-{dog.id}"' in html
        pager_tag = html[html.rindex('<div', 0, html.index(f'id="walk-pager-{dog.id}"')):
                         html.index(f'id="walk-pager-{dog.id}"')]
        assert 'd-none' in pager_tag


class TestDayBookings:

    def test_returns_that_days_bookings_in_slot_order(self, logged_in_client, client_user,
                                                      dog, service_type):
        day = _days(-10)
        _book(dog, client_user, service_type, day, 'Afternoon', note='Muddy')
        _book(dog, client_user, service_type, day, 'Morning')
        _book(dog, client_user, service_type, _days(-9))
        _book(dog, client_user, service_type, day, 'Morning', status='cancelled')

        data = logged_in_client.get(f'/day_bookings/{day.isoformat()}').get_json()
        assert data['success'] is True
        assert [b['slot'] for b in data['bookings']] == ['Morning', 'Afternoon']
        assert data['bookings'][1]['note'] == 'Muddy'
        assert all(b['dog_id'] == dog.id for b in data['bookings'])

    def test_excludes_other_households_and_revoked_co_owners(self, logged_in_client, client_user,
                                                             dog, service_type):
        day = _days(-10)
        other = make_user(email='other@test.dogboxx.org')
        other_dog = Dog(name='Other', gender='male')
        db.session.add(other_dog)
        db.session.flush()
        db.session.add(DogOwner(dog_id=other_dog.id, user_id=other.id, role='primary'))
        db.session.commit()
        # Booked by our client once, but they no longer own the dog.
        _book(other_dog, client_user, service_type, day)

        data = logged_in_client.get(f'/day_bookings/{day.isoformat()}').get_json()
        assert data['bookings'] == []

    def test_invalid_date_is_a_400(self, logged_in_client):
        resp = logged_in_client.get('/day_bookings/not-a-date')
        assert resp.status_code == 400

    def test_requires_login(self, client):
        resp = client.get(f'/day_bookings/{_days(-1).isoformat()}')
        assert resp.status_code in (302, 401)


class TestPastNotesAreViewOnly:

    def test_client_cannot_change_a_past_walks_note(self, logged_in_client, client_user,
                                                    dog, service_type):
        b = _book(dog, client_user, service_type, _days(-1), note='Muddy')
        resp = logged_in_client.post(f'/booking/{b.id}/note', json={'note': 'changed'})
        assert resp.status_code == 400
        assert db.session.get(Booking, b.id).client_notes == 'Muddy'

    def test_todays_walk_note_is_still_editable(self, logged_in_client, client_user,
                                                dog, service_type):
        b = _book(dog, client_user, service_type, _days(0))
        resp = logged_in_client.post(f'/booking/{b.id}/note', json={'note': 'Back gate'})
        assert resp.status_code == 200
        assert db.session.get(Booking, b.id).client_notes == 'Back gate'

    def test_admin_board_route_can_still_edit_a_past_note(self, app, client, client_user,
                                                          dog, service_type):
        # Admin notes go through board.update_booking_note_admin, which this
        # rule deliberately doesn't touch.
        admin = make_user(email='admin@test.dogboxx.org', role='walker', is_admin=True)
        b = _book(dog, client_user, service_type, _days(-1), note='Muddy')
        login(client, admin.email)
        resp = client.post(f'/admin/booking/{b.id}/note', json={'note': 'Fixed typo'})
        assert resp.status_code == 200
        assert db.session.get(Booking, b.id).client_notes == 'Fixed typo'
