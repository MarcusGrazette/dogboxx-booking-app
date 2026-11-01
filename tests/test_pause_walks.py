"""Client Pause Walks — preview and action (develop review #22).

The preview must pick exactly the bookings the action cancels (both go
through bookings.py::_pause_scope), label drop-ins, and flag per row whether
the cancellation will be charged — using the same bill_cancellation_for()
decision the action persists, so the warning matches the invoice.
"""
import datetime

import pytest

from app import db
from app.models import Booking, Dog, DogOwner, ServiceType
from app.utils.dates import local_today
from tests.conftest import make_user

NOTICE_DAYS = 5  # ServiceType default cancellation_notice_days


@pytest.fixture
def drop_in_type():
    st = ServiceType(name='Drop In', slug=ServiceType.DROP_IN, capacity_model='walker_assigned',
                     slot_type='morning_afternoon', requires_walker=True,
                     default_max_capacity=6, active=True)
    db.session.add(st)
    db.session.commit()
    return st


def _book(dog, user, service, days_ahead, slot='Morning', status='confirmed'):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=service.id,
                date=local_today() + datetime.timedelta(days=days_ahead),
                slot=slot, status=status)
    db.session.add(b)
    db.session.commit()
    return b.id


@pytest.fixture
def scenario(dog, client_user, service_type, drop_in_type):
    """A mixed range starting tomorrow and running past the notice window."""
    ids = {
        'late_walk':       _book(dog, client_user, service_type, 2),
        'late_requested':  _book(dog, client_user, service_type, 2, 'Afternoon', 'requested'),
        'late_drop_in':    _book(dog, client_user, drop_in_type, 3),
        'boundary_walk':   _book(dog, client_user, service_type, NOTICE_DAYS),
        'later_walk':      _book(dog, client_user, service_type, 10, 'Afternoon'),
        'out_of_range':    _book(dog, client_user, service_type, 20),
        'already_cancelled': _book(dog, client_user, service_type, 4, status='cancelled'),
    }
    # Someone else's dog in the same range is never touched.
    other = make_user(email='other@test.dogboxx.org')
    other_dog = Dog(name='Other', gender='male')
    db.session.add(other_dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=other_dog.id, user_id=other.id, role='primary'))
    db.session.commit()
    ids['other_household'] = _book(other_dog, other, service_type, 2)
    return ids


def _range(days_from=1, days_to=12):
    t = local_today()
    return ((t + datetime.timedelta(days=days_from)).isoformat(),
            (t + datetime.timedelta(days=days_to)).isoformat())


def _preview(flask_client, slots=()):
    start, end = _range()
    qs = ''.join(f'&slots={s}' for s in slots)
    return flask_client.get(f'/pause-walks/preview?start={start}&end={end}{qs}').get_json()


def _pause(flask_client, slots=()):
    start, end = _range()
    return flask_client.post('/pause-walks', json={'start': start, 'end': end,
                                                   'slots': list(slots)}).get_json()


class TestPausePreview:

    def test_rows_are_chronological_and_label_drop_ins(self, logged_in_client, scenario):
        data = _preview(logged_in_client)
        assert data['count'] == 5
        rows = data['bookings']
        assert [r['slot'] for r in rows[:2]] == ['Morning', 'Afternoon']  # same day, Morning first
        assert [r['is_drop_in'] for r in rows] == [False, False, True, False, False]

    def test_charged_flags_follow_the_billing_rule(self, logged_in_client, scenario):
        data = _preview(logged_in_client)
        # late confirmed walk + late confirmed drop-in are charged; a requested
        # booking never is; day NOTICE_DAYS is just outside the window.
        assert [r['charged'] for r in data['bookings']] == [True, False, True, False, False]
        assert data['charged_count'] == 2

    def test_slot_filter_narrows_the_preview(self, logged_in_client, scenario):
        data = _preview(logged_in_client, slots=['Afternoon'])
        assert {r['slot'] for r in data['bookings']} == {'Afternoon'}
        assert data['count'] == 2


class TestPauseMatchesPreview:

    @pytest.mark.parametrize('slots', [(), ('Morning',), ('Afternoon',)])
    def test_cancels_exactly_the_previewed_bookings_and_charges(self, logged_in_client,
                                                               scenario, slots):
        preview = _preview(logged_in_client, slots)
        result = _pause(logged_in_client, slots)
        assert result['cancelled_count'] == preview['count']

        cancelled = Booking.query.filter(Booking.status == 'cancelled',
                                         Booking.id != scenario['already_cancelled']).all()
        assert len(cancelled) == preview['count']
        assert sum(b.bill_cancellation for b in cancelled) == preview['charged_count']

    def test_leaves_out_of_range_and_other_households_alone(self, logged_in_client, scenario):
        _pause(logged_in_client)
        for key in ('out_of_range', 'other_household'):
            assert db.session.get(Booking, scenario[key]).status == 'confirmed'
