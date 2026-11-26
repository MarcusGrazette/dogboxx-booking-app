"""Characterization tests for the dashboard's per-slot figures (FEATURES #88).

/admin/api/month-data (calendar cells) and /admin/api/day-detail (side panel)
share one slot-stats helper; these pin the AM/PM figures both return:
walk counts by status, walk capacity, and drop-ins.
"""
import datetime

from app import db
from app.models import (
    Booking, Closure, Dog, DogOwner, ServiceType, Walker, WalkerAdHocAvailability,
    WalkerSchedule, WalkerUnavailability,
)
from tests.conftest import make_user

MONDAY = datetime.date(2030, 1, 7)
TUESDAY = MONDAY + datetime.timedelta(days=1)


def _service(name, slug):
    st = ServiceType(name=name, slug=slug, capacity_model='walker_assigned',
                     slot_type='morning_afternoon', requires_walker=True,
                     default_max_capacity=6, active=True)
    db.session.add(st)
    return st


def _walker(firstname):
    u = make_user(firstname=firstname, role='walker')
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    return w


def _setup():
    walk = _service('Group Walk', ServiceType.WALK)
    drop_in = _service('Drop In', ServiceType.DROP_IN)
    owner = make_user(firstname='Jane')
    dogs = [Dog(name=f'Dog{i}', breed='Mix') for i in range(6)]
    db.session.add_all(dogs)
    db.session.flush()
    db.session.add_all(DogOwner(dog_id=d.id, user_id=owner.id, role='primary') for d in dogs)

    # Monday Morning: Ann + Ben scheduled, Cal ad hoc -> capacity 3 x 6.
    # Monday Afternoon: Ben scheduled but unavailable, Cal ad hoc -> 1 x 6.
    ann, ben, cal = _walker('Ann'), _walker('Ben'), _walker('Cal')
    db.session.add_all([
        WalkerSchedule(walker_id=ann.id, day_of_week=0, slot='Morning', active=True),
        WalkerSchedule(walker_id=ben.id, day_of_week=0, slot='Morning', active=True),
        WalkerSchedule(walker_id=ben.id, day_of_week=0, slot='Afternoon', active=True),
        WalkerUnavailability(walker_id=ben.id, date=MONDAY, slot='Afternoon'),
        WalkerAdHocAvailability(walker_id=cal.id, date=MONDAY, slot='Morning'),
        WalkerAdHocAvailability(walker_id=cal.id, date=MONDAY, slot='Afternoon'),
        Closure(date=TUESDAY, range_id='c1'),
    ])

    def booking(service, dog, slot, status):
        db.session.add(Booking(user_id=owner.id, dog_id=dog.id, service_type_id=service.id,
                               date=MONDAY, slot=slot, status=status))

    booking(walk, dogs[0], 'Morning', 'confirmed')
    booking(walk, dogs[1], 'Morning', 'confirmed')
    booking(walk, dogs[2], 'Morning', 'requested')
    booking(walk, dogs[3], 'Morning', 'waitlisted')
    booking(walk, dogs[4], 'Morning', 'cancelled')        # not counted
    booking(drop_in, dogs[5], 'Morning', 'requested')
    booking(drop_in, dogs[0], 'Afternoon', 'confirmed')
    booking(walk, dogs[1], 'Afternoon', 'confirmed')
    booking(drop_in, dogs[2], 'Afternoon', 'waitlisted')  # to assign, like requested
    db.session.commit()


# drop_ins counts every active drop-in; drop_ins_to_assign only the requested
# and waitlisted ones the dashboard flags amber (FEATURES #94).
EXPECTED_AM = {'confirmed': 2, 'pending': 1, 'waitlist': 1, 'capacity': 18,
               'drop_ins': 1, 'drop_ins_to_assign': 1}
EXPECTED_PM = {'confirmed': 1, 'pending': 0, 'waitlist': 0, 'capacity': 6,
               'drop_ins': 2, 'drop_ins_to_assign': 1}


def _cell(data, day):
    for week in data['calendar_weeks']:
        for cell in week:
            if cell and cell['date_str'] == day.isoformat():
                return cell
    raise AssertionError(f'{day} not in month grid')


def test_month_data_slot_figures(logged_in_admin):
    _setup()
    resp = logged_in_admin.get('/admin/api/month-data?year=2030&month=1')
    assert resp.status_code == 200
    data = resp.get_json()
    monday = _cell(data, MONDAY)
    assert monday['am'] == EXPECTED_AM
    assert monday['pm'] == EXPECTED_PM
    assert monday['is_closed'] is False
    assert monday['unavailable_count'] == 1
    assert _cell(data, TUESDAY)['is_closed'] is True


def test_day_detail_slot_figures(logged_in_admin):
    _setup()
    resp = logged_in_admin.get(f'/admin/api/day-detail?date={MONDAY.isoformat()}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['am'] == EXPECTED_AM
    assert data['pm'] == EXPECTED_PM
    assert (data['scheduled_count'], data['extra_count'], data['unavailable_count']) == (1, 1, 1)


def test_day_with_no_walkers_has_zero_capacity(logged_in_admin):
    _setup()
    resp = logged_in_admin.get(f'/admin/api/day-detail?date={TUESDAY.isoformat()}')
    data = resp.get_json()
    zero = {'confirmed': 0, 'pending': 0, 'waitlist': 0, 'capacity': 0,
            'drop_ins': 0, 'drop_ins_to_assign': 0}
    assert data['am'] == zero and data['pm'] == zero
