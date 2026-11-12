"""
/book (walks) and /book_drop_in share one request path
(bookings.py::_create_booking_request). These pin what each route replies —
messages, statuses, payload fields — and what they share: the input checks,
the duplicate and closed-date 409s, and one commit for booking + notifications.
"""
import datetime
import uuid
from unittest.mock import patch

import pytest

from app import db
from app.blueprints.client.views import bookings as bookings_module
from app.models import Booking, BookingStatusChange, Closure
from tests import test_bookings as tb

WALK, DROP_IN = '/book', '/book_drop_in'


def _client_with_dog(email='req@test.com'):
    user = tb.make_user(email)
    tb.make_client_profile(user.id)
    dog = tb.make_dog()
    tb.attach_dog(dog.id, user.id)
    return user, dog


def _walker(day, slot='Morning', drop_ins=False, n=0):
    w = tb.make_walker_with_schedule(f'w{n}_{uuid.uuid4().hex[:6]}@test.com', day.weekday(), slot)
    w.does_drop_ins = drop_ins
    return w


def _setup(app, day, *, walk_capacity=6, drop_in_capacity=6, fill=None):
    """Client + dog, a walk-and-drop-in walker on `day`, both services.
    `fill='walk'|'drop-in'` books the slot full with another dog first."""
    with app.app_context():
        _walker(day, drop_ins=True)
        walk = tb.make_service(capacity=walk_capacity)
        drop_in = tb.make_drop_in_service(capacity=drop_in_capacity)
        if fill:
            other = tb.make_user(f'filler_{uuid.uuid4().hex[:6]}@test.com')
            other_dog = tb.make_dog('Filler')
            tb.attach_dog(other_dog.id, other.id)
            db.session.add(Booking(user_id=other.id, dog_id=other_dog.id,
                                   service_type_id=(walk if fill == 'walk' else drop_in).id,
                                   date=day, slot='Morning', status='confirmed'))
        user, dog = _client_with_dog()
        db.session.commit()
        return user.email, dog.id


def _post(client, endpoint, **payload):
    return client.post(endpoint, json=payload)


def _day_label(day):
    return day.strftime('%d %b')


# ── Replies ───────────────────────────────────────────────────────────────────

class TestWalkReplies:

    def test_auto_confirmed(self, app, client):
        day = tb.tomorrow()
        email, dog_id = _setup(app, day)
        tb.login(client, email)

        data = _post(client, WALK, dog_id=dog_id, date=day.isoformat(), slot='Morning').get_json()

        assert data['success'] is True and data['status'] == 'confirmed'
        assert data['message'] == f"Booking confirmed for Morning on {_day_label(day)}!"
        assert set(data['booking']) == {'id', 'date_display', 'date_iso', 'slot', 'status',
                                        'dog_id', 'walker_name', 'has_pickup_notes'}
        assert data['booking']['walker_name'] == 'Test'

    def test_auto_confirm_shares_one_batch_id(self, app, client):
        """Create + confirm rows share a batch_id, or the feed shows a stray
        "Requested" row."""
        day = tb.tomorrow()
        email, dog_id = _setup(app, day)
        tb.login(client, email)
        bid = _post(client, WALK, dog_id=dog_id, date=day.isoformat(),
                    slot='Morning').get_json()['booking']['id']

        rows = BookingStatusChange.query.filter_by(booking_id=bid).all()
        assert [r.to_status for r in rows] == ['requested', 'confirmed']
        assert rows[0].batch_id and rows[0].batch_id == rows[1].batch_id

    def test_waitlisted(self, app, client):
        day = tb.tomorrow()
        email, dog_id = _setup(app, day, walk_capacity=1, fill='walk')
        tb.login(client, email)

        data = _post(client, WALK, dog_id=dog_id, date=day.isoformat(), slot='Morning').get_json()

        assert data['status'] == 'waitlisted'
        assert data['message'] == (f"All slots are full — you've been added to the waitlist "
                                   f"for Morning on {_day_label(day)}.")

    def test_same_day(self, app, client):
        day = datetime.date.today()
        email, dog_id = _setup(app, day)
        tb.login(client, email)

        data = _post(client, WALK, dog_id=dog_id, date=day.isoformat(), slot='Morning').get_json()

        assert data['status'] == 'requested'
        owner = app.config['OWNER_FIRSTNAME']
        assert data['message'] == f"Same-day request submitted — {owner} will confirm shortly."


class TestDropInReplies:

    def test_requested_never_auto_confirms(self, app, client):
        day = tb.tomorrow()
        email, dog_id = _setup(app, day)
        tb.login(client, email)

        data = _post(client, DROP_IN, dog_id=dog_id, date=day.isoformat(), slot='Morning').get_json()

        assert data['success'] is True and data['status'] == 'requested'
        assert data['message'] == "Drop-in request submitted — we'll confirm shortly."
        assert data['booking']['is_drop_in'] is True
        assert set(data['booking']) == {'id', 'dog_id', 'date_display', 'date_iso', 'slot',
                                        'status', 'is_drop_in'}
        assert db.session.get(Booking, data['booking']['id']).walker_id is None

    def test_waitlisted(self, app, client):
        day = tb.tomorrow()
        email, dog_id = _setup(app, day, drop_in_capacity=1, fill='drop-in')
        tb.login(client, email)

        data = _post(client, DROP_IN, dog_id=dog_id, date=day.isoformat(), slot='Morning').get_json()

        assert data['status'] == 'waitlisted'
        assert data['message'] == (f"All drop-in slots are full for Morning on "
                                   f"{_day_label(day)}. You've been added to the waitlist.")

    def test_same_day(self, app, client):
        day = datetime.date.today()
        email, dog_id = _setup(app, day)
        tb.login(client, email)

        data = _post(client, DROP_IN, dog_id=dog_id, date=day.isoformat(), slot='Morning').get_json()

        assert data['status'] == 'requested'
        owner = app.config['OWNER_FIRSTNAME']
        assert data['message'] == f"Same-day drop-in request submitted — {owner} will confirm shortly."


# ── Shared checks ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('endpoint', [WALK, DROP_IN])
class TestSharedChecks:

    @pytest.mark.parametrize('payload,message', [
        ({'slot': 'Morning'}, 'Date and slot are required.'),
        ({'date': '2099-06-02'}, 'Date and slot are required.'),
        ({'date': '2099-06-02', 'slot': 'Evening'}, 'Invalid slot selected.'),
        ({'date': 'not-a-date', 'slot': 'Morning'}, 'Invalid date format.'),
        ({'date': '2000-01-03', 'slot': 'Morning'}, 'Booking date cannot be in the past.'),
    ])
    def test_bad_input_is_400(self, app, client, endpoint, payload, message):
        email, _ = _setup(app, tb.tomorrow())
        tb.login(client, email)
        resp = _post(client, endpoint, **payload)
        assert resp.status_code == 400
        assert resp.get_json() == {'success': False, 'message': message}

    def test_no_dog_is_400(self, app, client, endpoint):
        with app.app_context():
            user = tb.make_user('dogless@test.com')
            tb.make_client_profile(user.id)
            db.session.commit()
        tb.login(client, 'dogless@test.com')
        resp = _post(client, endpoint, date=tb.tomorrow().isoformat(), slot='Morning')
        assert resp.status_code == 400
        assert resp.get_json()['message'] == (
            'No dog found on your account. Please add a dog before booking.')

    def test_slot_already_booked_is_409(self, app, client, endpoint):
        """Across services: a drop-in blocks a walk in the same slot and vice versa."""
        day = tb.tomorrow()
        email, dog_id = _setup(app, day)
        tb.login(client, email)
        other = DROP_IN if endpoint == WALK else WALK
        assert _post(client, other, dog_id=dog_id, date=day.isoformat(),
                     slot='Morning').status_code == 200

        resp = _post(client, endpoint, dog_id=dog_id, date=day.isoformat(), slot='Morning')

        assert resp.status_code == 409
        label = 'drop in' if other == DROP_IN else 'group walk'
        assert resp.get_json()['message'] == f'Buddy already has a {label} booked for that slot.'
        assert Booking.query.count() == 1

    def test_closed_date_rejects_same_day_request(self, app, client, endpoint):
        """create_booking() lets same-day requests past the closure check, so
        the route's own check is what stops them."""
        day = datetime.date.today()
        email, dog_id = _setup(app, day)
        with app.app_context():
            db.session.add(Closure(date=day, reason='Holiday', range_id=uuid.uuid4().hex))
            db.session.commit()
        tb.login(client, email)

        resp = _post(client, endpoint, dog_id=dog_id, date=day.isoformat(), slot='Morning')

        assert resp.status_code == 409
        assert Booking.query.count() == 0

    def test_notification_failure_saves_nothing(self, app, client, endpoint):
        """Booking and its notifications commit together: if queuing the
        notifications fails, no booking is left behind."""
        day = tb.tomorrow()
        email, dog_id = _setup(app, day)
        tb.login(client, email)

        with patch.object(bookings_module, 'notify_created', side_effect=RuntimeError('boom')):
            resp = _post(client, endpoint, dog_id=dog_id, date=day.isoformat(), slot='Morning')

        assert resp.status_code == 500
        assert resp.get_json() == {'success': False,
                                   'message': 'An error occurred. Please try again.'}
        assert Booking.query.count() == 0


class TestMissingService:

    def test_walk_service_missing_is_500(self, app, client):
        with app.app_context():
            _client_with_dog()
            db.session.commit()
        tb.login(client, 'req@test.com')
        resp = _post(client, WALK, date=tb.tomorrow().isoformat(), slot='Morning')
        assert resp.status_code == 500
        assert resp.get_json()['message'] == 'No service type available. Please contact support.'

    def test_drop_in_service_off_is_503(self, app, client):
        with app.app_context():
            _client_with_dog()
            db.session.commit()
        tb.login(client, 'req@test.com')
        resp = _post(client, DROP_IN, date=tb.tomorrow().isoformat(), slot='Morning')
        assert resp.status_code == 503
        assert resp.get_json()['message'] == 'Drop-in service is not currently available.'


class TestHomeFormRetired:

    def test_post_to_home_is_405(self, app, client):
        """The no-JS booking form handler is gone; the page's JS always posts
        to /book, /book_both, /book_drop_in or /recurring_booking."""
        email, _ = _setup(app, tb.tomorrow())
        tb.login(client, email)
        resp = client.post('/', data={'date': tb.tomorrow().isoformat(), 'slot': 'Morning'})
        assert resp.status_code == 405
        assert Booking.query.count() == 0
