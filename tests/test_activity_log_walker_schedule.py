"""
PR 4/5 of the activity-feed expansion — WalkerSchedule diff capture in
app/blueprints/admin/views/walkers.py::walker_schedule (form route) and
walker_schedule_json (modal API route), both routed through
app/utils/activity_log.py::record_admin_action via the shared
_log_schedule_change helper.

Flagged in the plan doc as the structurally hardest call site: both routes
fully delete-and-reinsert a walker's WalkerSchedule rows on every save, with
no audit columns on the table at all, so the diff has to be computed from a
before/after set of (day_of_week, slot) pairs rather than a simple field
diff. Covers: added/removed diff correctness, the no-op case (resubmitting
an unchanged schedule logs nothing), actor attribution for both an admin and
a walker editing their own schedule (this route allows self-service, unlike
the rest of PR 2/3's call sites), and that the existing booking-reset
behaviour on removed combos is unaffected by this change.
"""
import datetime

import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import ActivityLog, Booking, Client, Dog, DogOwner, ServiceType, User, Walker, WalkerSchedule

TRUNCATE_ORDER = [
    'activity_logs', 'notifications', 'bookings', 'dog_owners', 'dogs',
    'walker_schedules', 'walkers', 'clients', 'service_types', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for t in TRUNCATE_ORDER:
            try:
                db.session.execute(text(f'DELETE FROM {t}'))
            except Exception:
                db.session.rollback()
        db.session.commit()
    yield


def _make_admin(email='sched_admin@test.com'):
    u = User(firstname='Admin', lastname='User', email=email, role='walker',
              is_admin=True, active=True,
              hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


def _make_walker(email='sched_walker@test.com', firstname='Walt', schedule=None):
    u = User(firstname=firstname, lastname='Walker', email=email, role='walker',
              active=True, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.flush()
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    for day, slot in (schedule or []):
        db.session.add(WalkerSchedule(walker_id=w.id, day_of_week=day, slot=slot, active=True))
    db.session.commit()
    return u, w


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _last_log():
    return ActivityLog.query.order_by(ActivityLog.id.desc()).first()


class TestScheduleJsonModal:
    """POST /admin/walkers/<walker_id>/schedule-json"""

    def test_added_and_removed_logged_with_correct_diff(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.id, admin.email
            _, walker = _make_walker(schedule=[(0, 'Morning'), (1, 'Morning')])
            walker_id = walker.id

        _login(client, admin_email[1])
        # Drop Mon AM, keep Tue AM, add Thu PM.
        resp = client.post(f'/admin/walkers/{walker_id}/schedule-json', json={
            'schedules': [{'day': 1, 'slot': 'Morning'}, {'day': 3, 'slot': 'Afternoon'}],
        })
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='walker_schedule', entity_id=walker_id).first()
            assert row is not None
            assert row.action == 'updated'
            assert row.actor_id == admin_email[0]
            assert row.changes['added'] == [[3, 'Afternoon']]
            assert row.changes['removed'] == [[0, 'Morning']]
            assert 'Added Thu PM' in row.summary
            assert 'removed Mon AM' in row.summary
            assert 'Walt Walker' in row.summary

    def test_no_change_logs_nothing(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            _, walker = _make_walker(schedule=[(0, 'Morning')])
            walker_id = walker.id

        _login(client, admin_email)
        resp = client.post(f'/admin/walkers/{walker_id}/schedule-json', json={
            'schedules': [{'day': 0, 'slot': 'Morning'}],
        })
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            assert ActivityLog.query.filter_by(entity_type='walker_schedule', entity_id=walker_id).count() == 0

    def test_additions_only_omit_removed_from_summary(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            _, walker = _make_walker(schedule=[])
            walker_id = walker.id

        _login(client, admin_email)
        resp = client.post(f'/admin/walkers/{walker_id}/schedule-json', json={
            'schedules': [{'day': 2, 'slot': 'Morning'}],
        })
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='walker_schedule', entity_id=walker_id).first()
            assert row is not None
            assert row.changes == {'added': [[2, 'Morning']], 'removed': []}
            assert row.summary.startswith('Added Wed AM')
            assert 'removed' not in row.summary

    def test_existing_booking_reset_behaviour_unchanged(self, app, client):
        """The added activity-log call must not interfere with the existing
        booking-reset-on-removed-combo behaviour (test_walker_schedule_modal_reset.py
        covers this in depth — this is a light guard that the two chokepoints
        compose correctly in the same request)."""
        monday = datetime.date.today() + datetime.timedelta(days=1)
        while monday.weekday() != 0:
            monday += datetime.timedelta(days=1)

        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            _, walker = _make_walker(schedule=[(0, 'Morning')])
            walker_id = walker.id

            owner = User(firstname='Jane', lastname='Smith', email='sched_owner@test.com',
                          role='client', active=True,
                          hashed_password=generate_password_hash('Testpass1!'))
            db.session.add(owner)
            db.session.flush()
            db.session.add(Client(user_id=owner.id, onboarding_completed=True))
            dog = Dog(name='Rex', gender='male', breed='Lab', allergies='')
            db.session.add(dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=owner.id, role='primary'))
            st = ServiceType(name='Group Walk', slug='group-walk', capacity_model='walker_assigned',
                              slot_type='morning_afternoon', requires_walker=True,
                              default_max_capacity=6, active=True)
            db.session.add(st)
            db.session.flush()
            booking = Booking(user_id=owner.id, dog_id=dog.id, service_type_id=st.id,
                                walker_id=walker_id, date=monday, slot='Morning', status='confirmed')
            db.session.add(booking)
            db.session.commit()
            booking_id = booking.id

        _login(client, admin_email)
        resp = client.post(f'/admin/walkers/{walker_id}/schedule-json', json={'schedules': []})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['affected_count'] == 1

        with app.app_context():
            b = db.session.get(Booking, booking_id)
            assert b.walker_id is None
            assert b.status == 'requested'

            row = ActivityLog.query.filter_by(entity_type='walker_schedule', entity_id=walker_id).first()
            assert row is not None
            assert row.changes['removed'] == [[0, 'Morning']]


class TestScheduleFormRoute:
    """POST /admin/walkers/<walker_id>/schedule (classic form, admin OR
    self-service walker)."""

    def _post_form(self, flask_client, walker_id, entries):
        """entries: iterable of (day_name, slot) e.g. ('monday', 'morning')."""
        data = {}
        for day_name, slot in entries:
            data[f'{day_name}-{slot}'] = 'y'
        return flask_client.post(f'/admin/walkers/{walker_id}/schedule', data=data,
                                  follow_redirects=True)

    def test_admin_edit_logs_diff(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.id, admin.email
            _, walker = _make_walker(schedule=[(0, 'Morning')])
            walker_id = walker.id

        _login(client, admin_email[1])
        self._post_form(client, walker_id, [('tuesday', 'afternoon')])

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='walker_schedule', entity_id=walker_id).first()
            assert row is not None
            assert row.actor_id == admin_email[0]
            assert row.changes['added'] == [[1, 'Afternoon']]
            assert row.changes['removed'] == [[0, 'Morning']]

    def test_walker_self_edit_attributes_to_walker(self, app, client):
        """A walker editing their own schedule (no admin involved) must be
        logged with their own id as actor — this route allows self-service,
        unlike PR 2/3's admin-only call sites."""
        with app.app_context():
            walker_user, walker = _make_walker(schedule=[])
            walker_user_id, walker_email = walker_user.id, walker_user.email
            walker_id = walker.id

        _login(client, walker_email)
        self._post_form(client, walker_id, [('wednesday', 'morning')])

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='walker_schedule', entity_id=walker_id).first()
            assert row is not None
            assert row.actor_id == walker_user_id
            assert row.changes == {'added': [[2, 'Morning']], 'removed': []}

    def test_resubmitting_same_schedule_logs_nothing(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            _, walker = _make_walker(schedule=[(4, 'Morning')])
            walker_id = walker.id

        _login(client, admin_email)
        self._post_form(client, walker_id, [('friday', 'morning')])

        with app.app_context():
            assert ActivityLog.query.filter_by(entity_type='walker_schedule', entity_id=walker_id).count() == 0
