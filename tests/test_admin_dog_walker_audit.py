"""
PR 3/5 of the activity-feed expansion — dog + walker (non-schedule) CRUD call
sites in app/blueprints/admin/views/dogs.py and .../walkers.py routed through
app/utils/admin_audit.py::record_admin_action.

Route-level integration tests: hit the real route via the `client` fixture,
then query AdminActionLog to assert one row landed with the expected
entity_type/action/summary substring, mirroring tests/test_admin_client_audit.py
(PR 2/5). One feed-rendering assertion covers the 'admin' bucket for this PR's
call sites.
"""
import datetime

import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import AdminActionLog, Client, Dog, DogOwner, User, Walker, WalkerSchedule

TRUNCATE_ORDER = [
    'admin_action_logs', 'notifications', 'bookings', 'dog_owners', 'dogs',
    'walker_schedules', 'walkers', 'clients', 'users',
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


def _make_admin(email='adwa_admin@test.com', super_admin=False):
    u = User(firstname='Admin', lastname='User', email=email, role='walker',
              is_admin=True, is_super_admin=super_admin, active=True,
              hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


def _make_dog_with_owner(email='owner@test.com'):
    owner = User(firstname='Jane', lastname='Smith', email=email, role='client',
                 active=True, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(owner)
    db.session.flush()
    db.session.add(Client(user_id=owner.id, onboarding_completed=True))
    # allergies='' (not None) matches what every write path (this route
    # included) actually persists — a None default here would make an
    # unrelated no-op edit look like a spurious allergies diff.
    dog = Dog(name='Rex', gender='male', breed='Lab', allergies='')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=owner.id, role='primary'))
    db.session.commit()
    return dog


def _make_walker(email='target_walker@test.com', firstname='Walt', active=True, schedule_days=None):
    u = User(firstname=firstname, lastname='Walker', email=email, role='walker',
              active=active, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.flush()
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    for day in (schedule_days or []):
        db.session.add(WalkerSchedule(walker_id=w.id, day_of_week=day, slot='Morning', active=active))
    db.session.commit()
    return u


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _last_log():
    return AdminActionLog.query.order_by(AdminActionLog.id.desc()).first()


class TestUpdateDog:

    def test_field_edit_logs_dog_row_with_diff(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            dog = _make_dog_with_owner()
            dog_id = dog.id

        _login(client, admin_email)
        client.post(f'/admin/dogs/{dog_id}/update', json={
            'name': 'Max', 'gender': 'male', 'breed': 'Lab',
            'allergies': '', 'date_of_birth': '', 'whatsapp_group_url': '', 'hold_key': False,
        })

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.action == 'updated'
            assert 'Max' in row.summary
            assert row.changes['name'] == ['Rex', 'Max']

    def test_no_op_edit_logs_nothing(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            dog = _make_dog_with_owner()
            dog_id = dog.id

        _login(client, admin_email)
        client.post(f'/admin/dogs/{dog_id}/update', json={
            'name': 'Rex', 'gender': 'male', 'breed': 'Lab',
            'allergies': '', 'date_of_birth': '', 'whatsapp_group_url': '', 'hold_key': False,
        })

        with app.app_context():
            assert AdminActionLog.query.count() == 0

    def test_pickup_instructions_change_is_redacted(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            dog = _make_dog_with_owner()
            dog_id = dog.id

        _login(client, admin_email)
        client.post(f'/admin/dogs/{dog_id}/update', json={
            'name': 'Rex', 'gender': 'male', 'breed': 'Lab',
            'allergies': '', 'date_of_birth': '', 'whatsapp_group_url': '', 'hold_key': False,
            'pickup_instructions': '<p>Gate code 4821</p>',
        })

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.changes.get('pickup_instructions') == ['(redacted)', '(redacted)']
            assert '4821' not in row.summary

    def test_pickup_photo_logs_filename_change(self, app, client):
        import io
        from PIL import Image
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            dog = _make_dog_with_owner()
            dog_id = dog.id

        _login(client, admin_email)
        buf = io.BytesIO()
        Image.new('RGB', (10, 10), color='blue').save(buf, format='PNG')
        buf.seek(0)
        resp = client.post(f'/admin/dogs/{dog_id}/pickup-photo',
                            data={'file': (buf, 'test.png')}, content_type='multipart/form-data')
        assert resp.status_code == 200, resp.data.decode()[:300]

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.changes['pickup_notes_photo'][0] is None
            assert row.changes['pickup_notes_photo'][1] is not None


class TestToggleWalkerAdmin:

    def test_grant_logs_walker_row(self, app, client):
        with app.app_context():
            admin = _make_admin(super_admin=True)
            admin_email = admin.email
            target = _make_walker()
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/walkers/{target_id}/toggle-admin')

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='walker', entity_id=target_id).first()
            assert row is not None
            assert row.action == 'updated'
            assert 'Granted' in row.summary
            assert row.changes['is_admin'] == [False, True]


class TestToggleWalkerDropIns:

    def test_toggle_logs_walker_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_walker()
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/walkers/{target_id}/toggle-drop-ins')

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='walker', entity_id=target_id).first()
            assert row is not None
            assert row.changes['does_drop_ins'] == [False, True]


class TestToggleWalkerClient:

    def test_create_branch_logs_created_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_walker()
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/walkers/{target_id}/toggle-client')

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='walker', entity_id=target_id).first()
            assert row is not None
            assert row.action == 'created'
            assert row.changes is None

    def test_delete_branch_logs_removed_row_with_name_before_delete(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_walker()
            target_id = target.id
            db.session.add(Client(user_id=target_id, onboarding_completed=True))
            db.session.commit()

        _login(client, admin_email)
        client.post(f'/admin/walkers/{target_id}/toggle-client')

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='walker', entity_id=target_id).first()
            assert row is not None
            assert row.action == 'removed'
            assert 'Walt Walker' in row.summary
            assert Client.query.filter_by(user_id=target_id).first() is None


class TestNewWalker:

    def test_creates_walker_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/walkers/new', data={
            'email': 'brandnew@test.com', 'firstname': 'Brand', 'lastname': 'New',
        })

        with app.app_context():
            row = _last_log()
            assert row is not None
            assert row.entity_type == 'walker'
            assert row.action == 'created'
            assert 'Brand New' in row.summary


class TestDeactivateActivateWalker:

    def test_deactivate_logs_combined_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_walker(schedule_days=[0, 1, 2])
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/walkers/{target_id}/deactivate')

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='walker', entity_id=target_id).first()
            assert row is not None
            assert row.changes['active'] == [True, False]
            assert '3 schedule slots cleared' in row.summary
            active_count = WalkerSchedule.query.filter_by(walker_id=Walker.query.filter_by(user_id=target_id).first().id, active=True).count()
            assert active_count == 0

    def test_activate_logs_combined_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_walker(active=False, schedule_days=[0, 1])
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/walkers/{target_id}/activate')

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='walker', entity_id=target_id).first()
            assert row is not None
            assert row.changes['active'] == [False, True]
            assert '2 schedule slots restored' in row.summary


class TestRemoveWalkerRole:

    def test_logs_combined_role_schedule_booking_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_walker(schedule_days=[0, 1])
            target_id = target.id
            db.session.add(Client(user_id=target_id, onboarding_completed=True))
            db.session.commit()

        _login(client, admin_email)
        client.post(f'/admin/walkers/{target_id}/remove-walker-role')

        with app.app_context():
            row = AdminActionLog.query.filter_by(entity_type='walker', entity_id=target_id).first()
            assert row is not None
            assert row.action == 'updated'
            assert row.changes['role'] == ['walker', 'client']
            assert '2 schedule slots cleared' in row.summary
            assert '0 bookings reset' in row.summary
            assert db.session.get(User, target_id).role == 'client'


class TestActivityFeedRendersDogWalkerRows:

    def test_dog_update_appears_under_admin_bucket(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            dog = _make_dog_with_owner()
            dog_id = dog.id

        _login(client, admin_email)
        client.post(f'/admin/dogs/{dog_id}/update', json={
            'name': 'Feedcheck', 'gender': 'male', 'breed': 'Lab',
            'allergies': '', 'date_of_birth': '', 'whatsapp_group_url': '', 'hold_key': False,
        })

        month = datetime.date.today().strftime('%Y-%m')
        resp = client.get(f'/admin/activity?month={month}')
        assert resp.status_code == 200
        assert b'data-activity="admin"' in resp.data
        assert b'Updated details for Feedcheck' in resp.data
