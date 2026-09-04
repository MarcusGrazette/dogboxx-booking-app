"""
PR 2/5 of the activity-feed expansion — client CRUD call sites in
app/blueprints/admin/views/clients.py routed through
app/utils/activity_log.py::record_admin_action.

Route-level integration tests: hit the real route via the `client` fixture,
then query ActivityLog to assert one row landed with the expected
entity_type/action/summary substring, mirroring TestRouteWiring in
tests/test_booking_status_log.py. One feed-rendering assertion covers the
'admin' bucket end-to-end (GET /admin/activity, `data-activity="admin"`).
"""
import datetime
import json
import uuid

import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import ActivityLog, Client, Dog, DogOwner, User

TRUNCATE_ORDER = [
    'activity_logs', 'notifications', 'dog_owners', 'dogs',
    'clients', 'users',
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


def _make_admin(email='cca_admin@test.com'):
    u = User(firstname='Admin', lastname='User', email=email, role='walker',
              is_admin=True, active=True,
              hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


def _make_client(email, with_dog=True):
    u = User(firstname='Jane', lastname='Smith', email=email, role='client',
              active=True, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.flush()
    db.session.add(Client(user_id=u.id, onboarding_completed=True))
    if with_dog:
        dog = Dog(name='Rex', gender='male', breed='Lab')
        db.session.add(dog)
        db.session.flush()
        db.session.add(DogOwner(dog_id=dog.id, user_id=u.id, role='primary'))
    db.session.commit()
    return u


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _base_form(**overrides):
    data = {
        'firstname': 'Jane',
        'lastname': 'Smith',
        'email': 'jane@test.com',
        # _make_client's User defaults email_marketing=True; WTForms
        # BooleanFields read absence-from-submit as False regardless of the
        # field's `default=`, so omitting this would register as a spurious
        # True->False diff on every edit-client POST in these tests.
        'notify_email': 'y',
    }
    data.update(overrides)
    return data


def _last_log():
    return ActivityLog.query.order_by(ActivityLog.id.desc()).first()


class TestNewClient:

    def test_creates_client_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/clients/new', data=_base_form(email='newperson@test.com'))

        with app.app_context():
            row = _last_log()
            assert row is not None
            assert row.entity_type == 'client'
            assert row.action == 'created'
            assert 'Jane Smith' in row.summary
            assert row.changes is None

    def test_creates_dog_row_when_dog_supplied(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/clients/new', data=_base_form(
            email='withdog@test.com', dog_name='Buddy', dog_gender='male',
        ))

        with app.app_context():
            rows = ActivityLog.query.filter_by(entity_type='dog').all()
            assert len(rows) == 1
            assert rows[0].action == 'created'
            assert 'Buddy' in rows[0].summary


class TestEditClient:

    def test_field_edit_logs_client_row_with_diff(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('orig@test.com', with_dog=False)
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/edit', data=_base_form(
            firstname='Janet', lastname='Smith', email='orig@test.com',
        ))

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='client', entity_id=target_id).first()
            assert row is not None
            assert row.action == 'updated'
            assert 'Janet Smith' in row.summary
            assert row.changes['firstname'] == ['Jane', 'Janet']

    def test_no_op_edit_logs_nothing(self, app, client):
        """Resubmitting the same values must not create a spurious row —
        diff_fields returning {} is the guard."""
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('sameval@test.com', with_dog=False)
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/edit', data=_base_form(
            firstname='Jane', lastname='Smith', email='sameval@test.com',
        ))

        with app.app_context():
            assert ActivityLog.query.count() == 0

    def test_redacted_field_change_omits_real_value_from_changes(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('addr@test.com', with_dog=False)
            target_id = target.id

        _login(client, admin_email)
        resp = client.post(f'/admin/clients/{target_id}/edit', data=_base_form(
            email='addr@test.com', address_line_1='42 Wallaby Way', postcode='SW1A 1AA',
        ))
        assert resp.status_code in (301, 302), resp.data.decode()[:600]

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='client', entity_id=target_id).first()
            assert row is not None
            assert row.changes.get('street_address') == ['(redacted)', '(redacted)']
            assert 'Wallaby Way' not in json.dumps(row.changes)
            assert 'Wallaby Way' not in row.summary

    def test_dog_field_edit_logs_separate_dog_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('withdog2@test.com', with_dog=True)
            target_id = target.id
            dog_owner = DogOwner.query.filter_by(user_id=target_id, role='primary').first()
            dog_id = dog_owner.dog_id

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/edit', data=_base_form(
            email='withdog2@test.com', dog_name='Rex II', dog_gender='male',
        ))

        with app.app_context():
            dog_row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert dog_row is not None
            assert dog_row.action == 'updated'
            assert dog_row.changes['name'] == ['Rex', 'Rex II']


class TestAddDog:

    def test_add_dog_logs_created_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('adddog@test.com', with_dog=False)
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/add-dog', data={
            'dog_name': 'Fido', 'dog_gender': 'male',
        })

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog').first()
            assert row is not None
            assert row.action == 'created'
            assert 'Fido' in row.summary


class TestActivateDeactivateClient:

    def test_deactivate_logs_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('deact@test.com', with_dog=False)
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/deactivate')

        with app.app_context():
            row = _last_log()
            assert row.entity_type == 'client'
            assert row.changes == {'active': [True, False]}
            assert 'Deactivated' in row.summary

    def test_reactivate_logs_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('react@test.com', with_dog=False)
            target.active = False
            target_id = target.id
            db.session.commit()

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/activate')

        with app.app_context():
            row = _last_log()
            assert row.changes == {'active': [False, True]}
            assert 'Activated' in row.summary

    def test_deactivating_already_inactive_client_logs_nothing(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('doubledeact@test.com', with_dog=False)
            target.active = False
            target_id = target.id
            db.session.commit()

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/deactivate')

        with app.app_context():
            assert ActivityLog.query.count() == 0


class TestJoinRevokeDogAccess:

    def test_join_logs_created_row_on_dog_entity(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            primary = _make_client('primary@test.com', with_dog=True)
            secondary = _make_client('secondary@test.com', with_dog=False)
            primary_id = primary.id
            secondary_id = secondary.id
            dog_owner = DogOwner.query.filter_by(user_id=primary_id, role='primary').first()
            dog_id = dog_owner.dog_id

        _login(client, admin_email)
        client.post(f'/admin/clients/{primary_id}/join',
                     data=json.dumps({'dog_id': dog_id, 'secondary_user_id': secondary_id}),
                     content_type='application/json')

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.action == 'created'
            assert 'Jane Smith' in row.summary  # secondary user's display name

    def test_revoke_logs_removed_row_with_baked_summary(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            primary = _make_client('primary2@test.com', with_dog=True)
            secondary = _make_client('secondary2@test.com', with_dog=False)
            primary_id = primary.id
            secondary_id = secondary.id
            dog_owner = DogOwner.query.filter_by(user_id=primary_id, role='primary').first()
            dog_id = dog_owner.dog_id
            db.session.add(DogOwner(dog_id=dog_id, user_id=secondary_id, role='secondary'))
            db.session.commit()

        _login(client, admin_email)
        client.post(f'/admin/clients/{primary_id}/revoke-access',
                     data=json.dumps({'dog_id': dog_id, 'secondary_user_id': secondary_id}),
                     content_type='application/json')

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id, action='removed').first()
            assert row is not None
            assert 'Rex' in row.summary


class TestPickupDetailsAndPhoto:

    def test_pickup_details_logs_dog_row_redacted(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('pickup@test.com', with_dog=True)
            target_id = target.id
            dog_owner = DogOwner.query.filter_by(user_id=target_id, role='primary').first()
            dog_id = dog_owner.dog_id

        _login(client, admin_email)
        resp = client.post(f'/admin/clients/{target_id}/pickup-details',
                     data=json.dumps({'pickup_instructions': '<p>Key under the mat</p>'}),
                     content_type='application/json')
        assert resp.status_code == 200, resp.data.decode()[:300]

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.changes.get('pickup_instructions') == ['(redacted)', '(redacted)']
            assert 'mat' not in row.summary
            assert 'mat' not in json.dumps(row.changes)

    def test_pickup_photo_logs_filename_change(self, app, client):
        import io
        from PIL import Image
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('photo@test.com', with_dog=True)
            target_id = target.id
            dog_owner = DogOwner.query.filter_by(user_id=target_id, role='primary').first()
            dog_id = dog_owner.dog_id

        _login(client, admin_email)
        # process_dog_photo runs Pillow's img.verify() — a real PNG built via
        # Image.save(), not hand-crafted bytes (which verify() rejects).
        buf = io.BytesIO()
        Image.new('RGB', (10, 10), color='red').save(buf, format='PNG')
        buf.seek(0)
        resp = client.post(f'/admin/clients/{target_id}/pickup-photo',
                            data={'file': (buf, 'test.png')}, content_type='multipart/form-data')

        assert resp.status_code == 200, resp.data.decode()[:300]
        assert resp.get_json()['success'] is True

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.changes['pickup_notes_photo'][0] is None
            assert row.changes['pickup_notes_photo'][1] is not None


class TestActivityFeedRendersClientRows:

    def test_client_edit_appears_under_admin_bucket(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            target = _make_client('feedcheck@test.com', with_dog=False)
            target_id = target.id

        _login(client, admin_email)
        client.post(f'/admin/clients/{target_id}/edit', data=_base_form(
            firstname='Renamed', lastname='Smith', email='feedcheck@test.com',
        ))

        month = datetime.date.today().strftime('%Y-%m')
        resp = client.get(f'/admin/activity?month={month}')
        assert resp.status_code == 200
        assert b'data-activity="admin"' in resp.data
        assert b'Updated contact details for Renamed Smith' in resp.data
