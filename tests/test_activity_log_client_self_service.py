"""
PR 6/5 (final) of the activity-feed expansion — client self-service call
sites in app/blueprints/client/views/profile.py routed through
app/utils/activity_log.py::record_admin_action.

Route-level integration tests: hit the real route via the `client` fixture,
then query ActivityLog to assert one row landed with the expected
entity_type/action/summary substring, mirroring
tests/test_activity_log_client.py (PR 2/5). Actor == subject here, so
summaries use "their own" wording rather than the admin-side "for <name>"
phrasing — asserted explicitly below. One feed-rendering assertion covers
the 'admin' bucket + 'client' actor_type combination end-to-end.
"""
import datetime

import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import ActivityLog, Client, Dog, DogOwner, User

TRUNCATE_ORDER = [
    'activity_logs', 'notifications', 'bookings', 'dog_owners', 'dogs',
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


def _make_admin(email='ss_admin@test.com'):
    u = User(firstname='Admin', lastname='User', email=email, role='walker',
              is_admin=True, active=True,
              hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


def _make_client_with_dog(email='jane@test.com'):
    u = User(firstname='Jane', lastname='Smith', email=email, role='client',
              active=True, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.flush()
    c = Client(user_id=u.id, onboarding_completed=True,
               street_address='1 Old Road', postal_code='OLD 123')
    db.session.add(c)
    dog = Dog(name='Rex', gender='male', breed='Lab', allergies='')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=u.id, role='primary'))
    db.session.commit()
    return u, dog


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _last_log():
    return ActivityLog.query.order_by(ActivityLog.id.desc()).first()


class TestProfilePostContactAndDogEdits:

    def _base_form(self, dog_name, dog_gender, dog_breed):
        return {
            'firstname': 'Jane', 'lastname': 'Smith',
            'address_line_1': '1 Old Road', 'postcode': 'OLD 123',
            'dog_name': dog_name, 'dog_gender': dog_gender, 'dog_breed': dog_breed or '',
            'notify_email': 'y',
        }

    def test_contact_info_edit_logs_client_row(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            user_id = user.id
            dog_name, dog_gender, dog_breed = dog.name, dog.gender, dog.breed

        _login(client, email)
        form = self._base_form(dog_name, dog_gender, dog_breed)
        form['firstname'] = 'Janet'
        form['address_line_1'] = '2 New Road'
        client.post('/profile', data=form)

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='client', entity_id=user_id).first()
            assert row is not None
            assert row.action == 'updated'
            assert row.actor_id == user_id
            assert 'their own' in row.summary
            assert row.changes['firstname'] == ['Jane', 'Janet']
            # street_address is a REDACTED_FIELDS member — key present, value not.
            assert row.changes['street_address'] == ['(redacted)', '(redacted)']
            assert 'New Road' not in row.summary

    def test_no_op_submit_logs_nothing(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            dog_name, dog_gender, dog_breed = dog.name, dog.gender, dog.breed

        _login(client, email)
        client.post('/profile', data=self._base_form(dog_name, dog_gender, dog_breed))

        with app.app_context():
            assert ActivityLog.query.count() == 0

    def test_pickup_instructions_edit_logs_dog_row(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            dog_id = dog.id
            dog_name, dog_gender, dog_breed = dog.name, dog.gender, dog.breed

        _login(client, email)
        form = self._base_form(dog_name, dog_gender, dog_breed)
        form[f'pickup_instructions_{dog_id}'] = '<p>Gate code 4821</p>'
        client.post('/profile', data=form)

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.action == 'updated'
            assert 'Rex' in row.summary
            assert 'their own' in row.summary
            assert row.changes['pickup_instructions'] == ['(redacted)', '(redacted)']
            assert '4821' not in row.summary


class TestUpdatePickupAjax:

    def test_edit_logs_dog_row(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            dog_id = dog.id

        _login(client, email)
        client.post('/profile/update-pickup', data={
            f'pickup_instructions_{dog_id}': '<p>New notes</p>',
        })

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.changes['pickup_instructions'] == ['(redacted)', '(redacted)']

    def test_resubmitting_same_notes_logs_nothing(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            dog_id = dog.id
            db.session.get(Dog, dog_id).pickup_instructions = '<p>Existing</p>'
            db.session.commit()

        _login(client, email)
        client.post('/profile/update-pickup', data={
            f'pickup_instructions_{dog_id}': '<p>Existing</p>',
        })

        with app.app_context():
            assert ActivityLog.query.count() == 0


class TestUpdateNotificationsAjax:

    def test_toggle_off_logs_client_row(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            user_id = user.id
            db.session.get(User, user_id).email_marketing = True
            db.session.commit()

        _login(client, email)
        client.post('/profile/update-notifications', data={'notify_email': 'false'})

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='client', entity_id=user_id).first()
            assert row is not None
            assert row.changes['email_marketing'] == [True, False]
            assert 'unsubscribed' in row.summary.lower()


class TestUpdateDogDetailsAjax:

    def test_dob_and_allergies_edit_logs_dog_row(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            dog_id = dog.id

        _login(client, email)
        client.post(f'/profile/dog/{dog_id}/update-details', data={
            'dob': '2020-01-01', 'health_notes': 'Peanut allergy',
        })

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            # _make_client_with_dog seeds allergies='' (matches every real write
            # path's empty-string default, not None — see test_activity_log_dog_walker.py).
            assert row.changes['allergies'] == ['', 'Peanut allergy']
            # allergies is NOT a REDACTED_FIELDS member — value stored plainly.
            assert 'Peanut allergy' not in row.summary  # summary stays entity-framed regardless


class TestUploadPickupPhotoAjax:

    def test_upload_logs_dog_row_with_filename_change(self, app, client):
        import io
        from PIL import Image
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            dog_id = dog.id

        _login(client, email)
        buf = io.BytesIO()
        Image.new('RGB', (10, 10), color='blue').save(buf, format='PNG')
        buf.seek(0)
        resp = client.post(f'/profile/dog/{dog_id}/pickup-photo',
                            data={'file': (buf, 'test.png')}, content_type='multipart/form-data')
        assert resp.status_code == 200, resp.data.decode()[:300]

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='dog', entity_id=dog_id).first()
            assert row is not None
            assert row.changes['pickup_notes_photo'][0] is None
            assert row.changes['pickup_notes_photo'][1] is not None
            assert 'pickup notes photo' in row.summary.lower()


class TestActivityFeedRendersClientSelfServiceRows:

    def test_client_edit_appears_under_admin_bucket_with_client_actor(self, app, client):
        with app.app_context():
            user, dog = _make_client_with_dog()
            email = user.email
            dog_id = dog.id

        _login(client, email)
        client.post('/profile/update-pickup', data={
            f'pickup_instructions_{dog_id}': '<p>Feed check</p>',
        })

        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        # /auth/login short-circuits (redirects, no re-auth) when a session is
        # already authenticated — must log out the client before logging in
        # as the admin to view the feed.
        client.post('/auth/logout')
        _login(client, admin_email)
        month = datetime.date.today().strftime('%Y-%m')
        resp = client.get(f'/admin/activity?month={month}')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-activity="admin"' in html
        assert 'data-actor="client"' in html
        assert 'Account changes' in html
