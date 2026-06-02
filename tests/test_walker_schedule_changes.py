"""
Tests for the unified Schedule Changes form on /walker/profile.

Covers the new batch endpoints (POST /walker/schedule-changes/batch and
POST /walker/schedule-changes/batch-delete) plus the server-side grouping
helper that powers the merged display list.
"""
import datetime
import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import (
    User, Walker, WalkerSchedule, WalkerUnavailability, WalkerAdHocAvailability,
    Booking, Dog, DogOwner, Client, ServiceType, Notification,
)


TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'walker_unavailabilities', 'walker_adhoc_availability',
    'walker_schedules', 'dog_owners', 'dogs', 'clients', 'walkers', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            try:
                db.session.execute(text(f'DELETE FROM {table}'))
            except Exception:
                db.session.rollback()
        db.session.commit()
    yield


def _make_walker(email='walker_sc@test.com', schedule_days=None):
    """Create a User + Walker. schedule_days is a list of (day_of_week, slot)
    tuples to seed into WalkerSchedule. day_of_week 0=Monday."""
    u = User(
        firstname='Walker', lastname='Sched', email=email,
        role='walker', is_admin=False, active=True,
        hashed_password=generate_password_hash('Testpass1!'),
    )
    db.session.add(u); db.session.flush()
    w = Walker(user_id=u.id)
    db.session.add(w); db.session.flush()
    for day, slot in (schedule_days or []):
        db.session.add(WalkerSchedule(
            walker_id=w.id, day_of_week=day, slot=slot, active=True
        ))
    db.session.commit()
    return u, w


def _login(client, email):
    return client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _next_weekday(target_dow):
    """Return the next date with weekday() == target_dow (0=Mon)."""
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != target_dow:
        d += datetime.timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Batch create endpoint
# ---------------------------------------------------------------------------

class TestScheduleChangesBatch:

    def test_single_date_both_slots_creates_two_unavailability_rows(self, app, client):
        """Walker scheduled Mon AM + PM marks themselves unavailable Mon Both."""
        with app.app_context():
            u, _ = _make_walker(schedule_days=[(0, 'Morning'), (0, 'Afternoon')])
            email = u.email
        _login(client, email)
        mon = _next_weekday(0)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': mon.isoformat(),
            'end_date':   mon.isoformat(),
            'slots':      ['Morning', 'Afternoon'],
            'type':       'unavailable',
            'reason':     'Doctor',
        })
        data = resp.get_json()
        assert data['success'] is True
        assert data['created'] == 2
        with app.app_context():
            rows = WalkerUnavailability.query.filter_by(date=mon).all()
            assert {r.slot for r in rows} == {'Morning', 'Afternoon'}
            assert all(r.reason == 'Doctor' for r in rows)

    def test_range_skips_weekends(self, app, client):
        """Mon→Sun range with Both slots → only Mon-Fri × 2 = 10 created.
        Sat + Sun silently dropped (DogBoxx is Mon–Fri)."""
        with app.app_context():
            u, _ = _make_walker(schedule_days=[
                (i, slot) for i in range(5) for slot in ('Morning', 'Afternoon')
            ])
            email = u.email
        _login(client, email)
        mon = _next_weekday(0)
        sun = mon + datetime.timedelta(days=6)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': mon.isoformat(),
            'end_date':   sun.isoformat(),
            'slots':      ['Morning', 'Afternoon'],
            'type':       'unavailable',
            'reason':     'Holiday',
        })
        data = resp.get_json()
        assert data['success'] is True
        assert data['created'] == 10        # 5 days × 2 slots
        # Weekend dates have no rows in DB
        with app.app_context():
            sat = mon + datetime.timedelta(days=5)
            assert WalkerUnavailability.query.filter_by(date=sat).count() == 0
            assert WalkerUnavailability.query.filter_by(date=sun).count() == 0

    def test_unavailable_creates_rows_for_unscheduled_slots(self, app, client):
        """Walker scheduled Mon AM only. Marks Mon Both unavailable → rows
        created for BOTH AM and PM, even though PM is unscheduled. This
        ensures the admin calendar can draw a continuous unavailability bar
        across the full declared range."""
        with app.app_context():
            u, _ = _make_walker(schedule_days=[(0, 'Morning')])
            email = u.email
        _login(client, email)
        mon = _next_weekday(0)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': mon.isoformat(),
            'end_date':   mon.isoformat(),
            'slots':      ['Morning', 'Afternoon'],
            'type':       'unavailable',
        })
        data = resp.get_json()
        assert data['created'] == 2
        assert data['skipped'] == 0

    def test_available_skips_slots_walker_already_works(self, app, client):
        """Walker scheduled Mon AM. Tries to add Mon AM as available → skipped
        (already scheduled). Adding Mon PM as adhoc-available → created."""
        with app.app_context():
            u, _ = _make_walker(schedule_days=[(0, 'Morning')])
            email = u.email
        _login(client, email)
        mon = _next_weekday(0)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': mon.isoformat(),
            'end_date':   mon.isoformat(),
            'slots':      ['Morning', 'Afternoon'],
            'type':       'available',
        })
        data = resp.get_json()
        assert data['created'] == 1
        assert data['skipped'] == 1
        with app.app_context():
            adhoc = WalkerAdHocAvailability.query.filter_by(date=mon).all()
            assert len(adhoc) == 1
            assert adhoc[0].slot == 'Afternoon'

    def test_end_before_start_rejected(self, app, client):
        with app.app_context():
            u, _ = _make_walker()
            email = u.email
        _login(client, email)
        d = _next_weekday(0)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': d.isoformat(),
            'end_date':   (d - datetime.timedelta(days=1)).isoformat(),
            'slots':      ['Morning'],
            'type':       'available',
        })
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_range_over_90_days_rejected(self, app, client):
        with app.app_context():
            u, _ = _make_walker()
            email = u.email
        _login(client, email)
        d = _next_weekday(0)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': d.isoformat(),
            'end_date':   (d + datetime.timedelta(days=95)).isoformat(),
            'slots':      ['Morning'],
            'type':       'available',
        })
        assert resp.status_code == 400
        assert '90' in resp.get_json()['message']

    def test_past_start_date_rejected(self, app, client):
        with app.app_context():
            u, _ = _make_walker()
            email = u.email
        _login(client, email)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': (datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
            'slots':      ['Morning'],
            'type':       'available',
        })
        assert resp.status_code == 400

    def test_idempotent_on_duplicate(self, app, client):
        """Submitting the same range twice doesn't double-write."""
        with app.app_context():
            u, _ = _make_walker(schedule_days=[(0, 'Morning')])
            email = u.email
        _login(client, email)
        mon = _next_weekday(0)
        payload = {
            'start_date': mon.isoformat(),
            'slots':      ['Morning'],
            'type':       'unavailable',
        }
        client.post('/walker/schedule-changes/batch', json=payload)
        resp = client.post('/walker/schedule-changes/batch', json=payload)
        data = resp.get_json()
        assert data['created'] == 0
        assert data['skipped'] == 1
        with app.app_context():
            assert WalkerUnavailability.query.filter_by(date=mon).count() == 1


# ---------------------------------------------------------------------------
# Batch delete endpoint
# ---------------------------------------------------------------------------

class TestScheduleChangesBatchDelete:

    def test_delete_group_removes_all_rows(self, app, client):
        """A Mon–Fri Both holiday creates 10 rows. Batch-deleting by the IDs
        the renderer collected removes every row in one request."""
        with app.app_context():
            u, w = _make_walker(schedule_days=[
                (i, slot) for i in range(5) for slot in ('Morning', 'Afternoon')
            ])
            email = u.email
        _login(client, email)
        mon = _next_weekday(0)
        fri = mon + datetime.timedelta(days=4)
        client.post('/walker/schedule-changes/batch', json={
            'start_date': mon.isoformat(),
            'end_date':   fri.isoformat(),
            'slots':      ['Morning', 'Afternoon'],
            'type':       'unavailable',
        })
        with app.app_context():
            ids = [r.id for r in WalkerUnavailability.query.all()]
            assert len(ids) == 10

        resp = client.post('/walker/schedule-changes/batch-delete', json={
            'unavail_ids': ids,
        })
        assert resp.get_json() == {'success': True, 'deleted': 10}
        with app.app_context():
            assert WalkerUnavailability.query.count() == 0

    def test_cannot_delete_other_walkers_rows(self, app, client):
        """IDs that belong to a different walker are silently ignored."""
        with app.app_context():
            u1, _ = _make_walker(email='w1@test.com',
                                 schedule_days=[(0, 'Morning')])
            u2, w2 = _make_walker(email='w2@test.com',
                                  schedule_days=[(0, 'Morning')])
            mon = _next_weekday(0)
            row = WalkerUnavailability(walker_id=w2.id, date=mon, slot='Morning')
            db.session.add(row); db.session.commit()
            other_id = row.id

        _login(client, 'w1@test.com')
        resp = client.post('/walker/schedule-changes/batch-delete', json={
            'unavail_ids': [other_id],
        })
        assert resp.get_json()['deleted'] == 0
        with app.app_context():
            # Row still there
            assert WalkerUnavailability.query.get(other_id) is not None


# ---------------------------------------------------------------------------
# Server-side grouping helper
# ---------------------------------------------------------------------------

class TestScheduleChangeGrouping:
    """Direct unit tests for _build_schedule_change_groups — the rendering
    helper that collapses contiguous DB rows into display rows."""

    def test_morning_plus_afternoon_same_day_becomes_both(self, app):
        from app.blueprints.walker.routes import _build_schedule_change_groups
        with app.app_context():
            u, w = _make_walker(schedule_days=[(0, 'Morning'), (0, 'Afternoon')])
            mon = _next_weekday(0)
            db.session.add(WalkerUnavailability(walker_id=w.id, date=mon, slot='Morning', reason='X'))
            db.session.add(WalkerUnavailability(walker_id=w.id, date=mon, slot='Afternoon', reason='X'))
            db.session.commit()
            rows = WalkerUnavailability.query.all()
            groups = _build_schedule_change_groups([], rows)
        assert len(groups) == 1
        g = groups[0]
        assert g['slot_label'] == 'Both'
        assert g['type'] == 'unavailable'
        assert g['start_date'] == mon
        assert g['end_date'] == mon
        assert g['is_range'] is False
        assert len(g['unavail_ids']) == 2

    def test_contiguous_days_collapse_into_one_range(self, app):
        from app.blueprints.walker.routes import _build_schedule_change_groups
        with app.app_context():
            u, w = _make_walker()
            mon = _next_weekday(0)
            for offset in range(5):  # Mon–Fri
                d = mon + datetime.timedelta(days=offset)
                db.session.add(WalkerUnavailability(walker_id=w.id, date=d, slot='Morning', reason='Holiday'))
                db.session.add(WalkerUnavailability(walker_id=w.id, date=d, slot='Afternoon', reason='Holiday'))
            db.session.commit()
            rows = WalkerUnavailability.query.all()
            groups = _build_schedule_change_groups([], rows)
        assert len(groups) == 1
        g = groups[0]
        assert g['is_range'] is True
        assert g['start_date'] == mon
        assert g['end_date'] == mon + datetime.timedelta(days=4)
        assert g['slot_label'] == 'Both'
        assert len(g['unavail_ids']) == 10

    def test_different_reason_breaks_grouping(self, app):
        from app.blueprints.walker.routes import _build_schedule_change_groups
        with app.app_context():
            u, w = _make_walker()
            mon = _next_weekday(0)
            tue = mon + datetime.timedelta(days=1)
            db.session.add(WalkerUnavailability(walker_id=w.id, date=mon, slot='Morning', reason='A'))
            db.session.add(WalkerUnavailability(walker_id=w.id, date=tue, slot='Morning', reason='B'))
            db.session.commit()
            rows = WalkerUnavailability.query.all()
            groups = _build_schedule_change_groups([], rows)
        assert len(groups) == 2

    def test_gap_breaks_grouping(self, app):
        """Wednesday gap between Mon and Thu → two separate groups."""
        from app.blueprints.walker.routes import _build_schedule_change_groups
        with app.app_context():
            u, w = _make_walker()
            mon = _next_weekday(0)
            thu = mon + datetime.timedelta(days=3)
            db.session.add(WalkerUnavailability(walker_id=w.id, date=mon, slot='Morning', reason='X'))
            db.session.add(WalkerUnavailability(walker_id=w.id, date=thu, slot='Morning', reason='X'))
            db.session.commit()
            rows = WalkerUnavailability.query.all()
            groups = _build_schedule_change_groups([], rows)
        assert len(groups) == 2
        assert groups[0]['start_date'] == mon
        assert groups[1]['start_date'] == thu


# ---------------------------------------------------------------------------
# Session 3: client booking_reset notifications on reset paths (§7.1)
# ---------------------------------------------------------------------------

def _make_client_with_dog(email):
    """Create a client user + Client profile + Dog + DogOwner. Returns (user, dog)."""
    from werkzeug.security import generate_password_hash
    u = User(firstname='Client', lastname='Test', email=email, role='client',
             hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u); db.session.flush()
    db.session.add(Client(user_id=u.id, onboarding_completed=True)); db.session.flush()
    dog = Dog(name='Rover', breed='Mixed'); db.session.add(dog); db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=u.id, role='primary'))
    db.session.commit()
    return u, dog


def _make_service():
    st = ServiceType(name='Group Walk', slug='group-walk',
                     capacity_model='walker_assigned', slot_type='morning_afternoon',
                     requires_walker=True, default_max_capacity=6, active=True)
    db.session.add(st); db.session.commit()
    return st


def _confirmed_booking(user_id, dog_id, service_type_id, walker_id, date, slot='Morning'):
    b = Booking(user_id=user_id, dog_id=dog_id, service_type_id=service_type_id,
                date=date, slot=slot, status='confirmed', walker_id=walker_id)
    db.session.add(b); db.session.commit()
    return b


class TestClientResetNotifications:
    """Walker self-service unavailability resets must notify affected clients (§7.1)."""

    def test_single_unavailability_notifies_client(self, app, client):
        """POST /walker/unavailability with a slot that has a confirmed booking
        → the booking owner gets exactly one booking_reset notification."""
        monday = _next_weekday(0)
        with app.app_context():
            walker_u, walker = _make_walker(
                email='walker_notif@test.com',
                schedule_days=[(0, 'Morning')],
            )
            client_u, dog = _make_client_with_dog('client_notif@test.com')
            st = _make_service()
            _confirmed_booking(client_u.id, dog.id, st.id, walker.id, monday)
            walker_email = walker_u.email
            client_uid = client_u.id

        _login(client, walker_email)
        resp = client.post('/walker/unavailability', json={
            'date': monday.isoformat(),
            'slot': 'Morning',
        })
        assert resp.status_code == 201

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=client_uid).all()
            assert len(notifs) == 1
            n = notifs[0]
            assert n.notification_type == 'system'
            assert 'needs a new walker' in n.title
            assert 'No action needed' in n.body

    def test_batch_unavailable_notifies_client(self, app, client):
        """POST /walker/schedule-changes/batch type=unavailable with affected
        confirmed bookings → the booking owner gets exactly one grouped booking_reset."""
        monday = _next_weekday(0)
        tuesday = monday + datetime.timedelta(days=1)  # always after monday
        with app.app_context():
            walker_u, walker = _make_walker(
                email='walker_batch_notif@test.com',
                schedule_days=[(0, 'Morning'), (1, 'Morning')],
            )
            client_u, dog = _make_client_with_dog('client_batch_notif@test.com')
            st = _make_service()
            # Two confirmed bookings across two days — both reset by the batch.
            _confirmed_booking(client_u.id, dog.id, st.id, walker.id, monday)
            _confirmed_booking(client_u.id, dog.id, st.id, walker.id, tuesday, 'Morning')
            walker_email = walker_u.email
            client_uid = client_u.id

        _login(client, walker_email)
        resp = client.post('/walker/schedule-changes/batch', json={
            'start_date': monday.isoformat(),
            'end_date':   tuesday.isoformat(),
            'slots':      ['Morning'],
            'type':       'unavailable',
        })
        assert resp.get_json()['success'] is True

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=client_uid).all()
            # Two bookings → one grouped notification.
            assert len(notifs) == 1
            n = notifs[0]
            assert n.notification_type == 'system'
            assert '2 of your walks' in n.title
            assert 'No action needed' in n.body


class TestRemoveWalkerRoleResetNotification:
    """Removing a walker's role unassigns their confirmed walks — the affected
    client must get a booking_reset, same as deactivate_walker (F1 / §7.2)."""

    def test_remove_walker_role_notifies_affected_client(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            # Admin who performs the action.
            admin = User(firstname='Admin', lastname='Boss', email='admin_rwr@test.com',
                         role='walker', is_admin=True, active=True,
                         hashed_password=generate_password_hash('Testpass1!'))
            db.session.add(admin); db.session.commit()
            admin_email = admin.email

            # Dual-role walker (must have a Client record for the role removal).
            walker_u, walker = _make_walker(email='walker_rwr@test.com',
                                            schedule_days=[(0, 'Morning')])
            db.session.add(Client(user_id=walker_u.id, onboarding_completed=True))
            db.session.commit()
            walker_user_id = walker_u.id

            # A separate client whose confirmed walk is assigned to that walker.
            client_u, dog = _make_client_with_dog('client_rwr@test.com')
            st = _make_service()
            _confirmed_booking(client_u.id, dog.id, st.id, walker.id, monday)
            client_uid = client_u.id
            dog_id = dog.id

        _login(client, admin_email)
        resp = client.post(f'/admin/walkers/{walker_user_id}/remove-walker-role')
        assert resp.get_json()['success'] is True

        with app.app_context():
            # Booking reset to pending + unassigned.
            b = Booking.query.filter_by(dog_id=dog_id).first()
            assert b.status == 'requested'
            assert b.walker_id is None
            # Client told exactly once, with the reset wording.
            notifs = Notification.query.filter_by(recipient_id=client_uid).all()
            assert len(notifs) == 1
            assert notifs[0].notification_type == 'system'
            assert 'needs a new walker' in notifs[0].title
