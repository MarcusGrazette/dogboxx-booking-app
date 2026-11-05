"""Characterization tests for the two board-data endpoints (FEATURES #87).

/admin/api/board-data (walks — the dashboard's Assign modal) and
/admin/api/drop-in-board-data share one builder. These pin each endpoint's
whole JSON payload so the walk-only parts (the both-slots flag, frozen_slots)
and the drop-in-only walker filter (does_drop_ins) can't drift.
"""
import datetime
from unittest.mock import patch

from app import db
from app.models import (
    Booking, Dog, DogOwner, ServiceType, SlotFreeze, Walker, WalkerAdHocAvailability,
    WalkerSchedule, WalkerUnavailability,
)
from tests.conftest import make_user

MONDAY = datetime.date(2030, 1, 7)
DATE = MONDAY.isoformat()


def _service(name, slug):
    st = ServiceType(name=name, slug=slug, capacity_model='walker_assigned',
                     slot_type='morning_afternoon', requires_walker=True,
                     default_max_capacity=6, active=True)
    db.session.add(st)
    return st


def _walker(firstname, *, does_drop_ins):
    u = make_user(firstname=firstname, role='walker')
    w = Walker(user_id=u.id, does_drop_ins=does_drop_ins)
    db.session.add(w)
    db.session.flush()
    return w


def _setup():
    walk = _service('Group Walk', ServiceType.WALK)
    drop_in = _service('Drop In', ServiceType.DROP_IN)
    owner = make_user(firstname='Jane', lastname='Smith')
    dog = Dog(name='Rex', breed='Collie')
    other_dog = Dog(name='Bo', breed='Pug')
    db.session.add_all([dog, other_dog])
    db.session.flush()
    db.session.add_all([
        DogOwner(dog_id=dog.id, user_id=owner.id, role='primary'),
        DogOwner(dog_id=other_dog.id, user_id=owner.id, role='primary'),
    ])

    # Ann does drop-ins (scheduled Morning); Ben doesn't (scheduled both
    # slots, unavailable Afternoon); Cal is a drop-in walker on an ad hoc day.
    ann = _walker('Ann', does_drop_ins=True)
    ben = _walker('Ben', does_drop_ins=False)
    cal = _walker('Cal', does_drop_ins=True)
    db.session.add_all([
        WalkerSchedule(walker_id=ann.id, day_of_week=0, slot='Morning', active=True),
        WalkerSchedule(walker_id=ben.id, day_of_week=0, slot='Morning', active=True),
        WalkerSchedule(walker_id=ben.id, day_of_week=0, slot='Afternoon', active=True),
        WalkerUnavailability(walker_id=ben.id, date=MONDAY, slot='Afternoon'),
        WalkerAdHocAvailability(walker_id=cal.id, date=MONDAY, slot='Afternoon'),
        SlotFreeze(date=MONDAY, slot='Afternoon', range_id='r1'),
    ])

    def booking(service, d, slot, status, walker=None):
        b = Booking(user_id=owner.id, dog_id=d.id, service_type_id=service.id,
                    date=MONDAY, slot=slot, status=status,
                    walker_id=walker.id if walker else None,
                    pickup_order=1 if walker else None,
                    client_notes='Gate code 12' if walker else None)
        db.session.add(b)
        return b

    # Rex has both walks -> has_both_slots on the walk board.
    walk_am = booking(walk, dog, 'Morning', 'confirmed', ben)
    walk_pm = booking(walk, dog, 'Afternoon', 'requested')
    booking(walk, other_dog, 'Morning', 'cancelled')                 # never shown
    drop_am = booking(drop_in, other_dog, 'Afternoon', 'confirmed', cal)
    drop_wl = booking(drop_in, other_dog, 'Morning', 'waitlisted')
    db.session.commit()
    return dict(walk_am=walk_am, walk_pm=walk_pm, drop_am=drop_am, drop_wl=drop_wl,
                ann=ann, ben=ben, cal=cal)


def _card(b, **extra):
    d = {
        'id': b.id, 'dog_name': b.dog.name, 'dog_pic': None,
        'owner_name': 'Jane', 'slot': b.slot, 'status': b.status,
        'pickup_order': b.pickup_order, 'walker_id': b.walker_id,
        'has_notes': False, 'client_notes': b.client_notes,
    }
    d.update(extra)
    return d


def _by_id(items):
    return sorted(items, key=lambda x: x['id'])


class TestBoardDataPayload:

    def test_walk_board(self, logged_in_admin):
        s = _setup()
        resp = logged_in_admin.get(f'/admin/api/board-data/{DATE}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data) == {'success', 'date', 'pending', 'assigned', 'walkers',
                             'max_capacity', 'frozen_slots'}
        assert data['success'] is True and data['date'] == DATE
        assert data['pending'] == [_card(s['walk_pm'], has_both_slots=True)]
        assert data['assigned'] == [_card(s['walk_am'], has_both_slots=True)]
        assert _by_id(data['walkers']) == _by_id([
            {'id': s['ann'].id, 'name': 'Ann', 'available_slots': ['Morning'],
             'unavailable_slots': []},
            {'id': s['ben'].id, 'name': 'Ben', 'available_slots': ['Morning'],
             'unavailable_slots': ['Afternoon']},
            {'id': s['cal'].id, 'name': 'Cal', 'available_slots': ['Afternoon'],
             'unavailable_slots': []},
        ])
        assert data['max_capacity'] == 6
        assert data['frozen_slots'] == ['Afternoon']

    def test_drop_in_board(self, logged_in_admin):
        s = _setup()
        resp = logged_in_admin.get(f'/admin/api/drop-in-board-data/{DATE}')
        assert resp.status_code == 200
        data = resp.get_json()
        # No frozen_slots: drop-ins never auto-confirm, so a freeze doesn't apply.
        assert set(data) == {'success', 'date', 'pending', 'assigned', 'walkers',
                             'max_capacity'}
        assert data['success'] is True and data['date'] == DATE
        # No has_both_slots key on drop-in cards.
        assert data['pending'] == [_card(s['drop_wl'])]
        assert data['assigned'] == [_card(s['drop_am'])]
        # Ben doesn't do drop-ins, so he isn't on this board.
        assert _by_id(data['walkers']) == _by_id([
            {'id': s['ann'].id, 'name': 'Ann', 'available_slots': ['Morning'],
             'unavailable_slots': []},
            {'id': s['cal'].id, 'name': 'Cal', 'available_slots': ['Afternoon'],
             'unavailable_slots': []},
        ])
        assert data['max_capacity'] == 6


class TestBoardDataErrors:

    def test_invalid_date_is_400(self, logged_in_admin):
        for url in ('/admin/api/board-data/nope', '/admin/api/drop-in-board-data/nope'):
            resp = logged_in_admin.get(url)
            assert resp.status_code == 400
            assert resp.get_json()['message'] == 'Invalid date'

    def test_missing_service_type_is_500_on_both_boards(self, logged_in_admin):
        """Unseeded DB: neither board falls back to showing every service."""
        for url in (f'/admin/api/board-data/{DATE}', f'/admin/api/drop-in-board-data/{DATE}'):
            resp = logged_in_admin.get(url)
            assert resp.status_code == 500
            assert resp.get_json()['success'] is False

    def test_unexpected_error_is_logged_json_500_on_both_boards(self, logged_in_admin):
        _setup()
        for url in (f'/admin/api/board-data/{DATE}', f'/admin/api/drop-in-board-data/{DATE}'):
            with patch('app.blueprints.admin.views.board._batch_owners_display',
                       side_effect=RuntimeError('boom')):
                resp = logged_in_admin.get(url)
            assert resp.status_code == 500
            assert resp.get_json() == {'success': False, 'message': 'Could not load board data'}
