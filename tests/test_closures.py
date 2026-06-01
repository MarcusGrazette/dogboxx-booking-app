"""
Tests for /admin/closures — Session 3 (§7.4).

Verifies that creating a closure notifies co-owners and the assigned walker
in addition to the primary booking owner.
"""
import json
import datetime

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    User, Client, Walker, Dog, DogOwner, Booking, ServiceType, Notification,
)


def _next_weekday(target_dow):
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != target_dow:
        d += datetime.timedelta(days=1)
    return d


def _make_user(email, role='client', is_admin=False):
    u = User(firstname='Test', lastname='User', email=email, role=role,
             is_admin=is_admin, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


def _login(flask_client, email):
    return flask_client.post('/auth/login',
                             data={'email': email, 'password': 'Testpass1!'},
                             follow_redirects=True)


def _post_closure(flask_client, date_str, reason=None):
    return flask_client.post(
        '/admin/closures',
        data=json.dumps({'date': date_str, 'reason': reason}),
        content_type='application/json',
    )


class TestClosureFanOut:
    """Closure cancellations must reach co-owners and the assigned walker."""

    def _setup(self, app):
        """Create: admin, primary owner, co-owner, walker, dog, service, booking."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('closure_admin@test.com', role='walker', is_admin=True)
            primary = _make_user('closure_primary@test.com', role='client')
            co_owner = _make_user('closure_coowner@test.com', role='client')
            walker_u = _make_user('closure_walker@test.com', role='walker')
            walker = Walker(user_id=walker_u.id)
            db.session.add(walker); db.session.flush()
            db.session.add(Client(user_id=primary.id, onboarding_completed=True))
            db.session.add(Client(user_id=co_owner.id, onboarding_completed=True))
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned', slot_type='morning_afternoon',
                requires_walker=True, default_max_capacity=6, active=True,
            )
            db.session.add(st); db.session.flush()
            dog = Dog(name='Luna', breed='Spaniel')
            db.session.add(dog); db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=primary.id, role='primary'))
            db.session.add(DogOwner(dog_id=dog.id, user_id=co_owner.id, role='secondary'))
            booking = Booking(
                user_id=primary.id, dog_id=dog.id, service_type_id=st.id,
                date=monday, slot='Morning', status='confirmed',
                walker_id=walker.id,
            )
            db.session.add(booking); db.session.commit()
            return {
                'monday': monday,
                'admin_email': admin.email,
                'primary_id': primary.id,
                'co_owner_id': co_owner.id,
                'walker_uid': walker_u.id,
            }

    def test_closure_notifies_primary_owner(self, app, client):
        ids = self._setup(app)
        _login(client, ids['admin_email'])
        resp = _post_closure(client, ids['monday'].isoformat())
        assert resp.get_json()['cancelled_count'] == 1

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=ids['primary_id']).all()
            assert len(notifs) == 1
            assert notifs[0].notification_type == 'booking_cancelled'

    def test_closure_notifies_co_owner(self, app, client):
        """Co-owner must also receive a booking_cancelled notification (§7.4)."""
        ids = self._setup(app)
        _login(client, ids['admin_email'])
        _post_closure(client, ids['monday'].isoformat())

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=ids['co_owner_id']).all()
            assert len(notifs) == 1, "co-owner must get exactly one notification"
            assert notifs[0].notification_type == 'booking_cancelled'
            assert 'Luna' in notifs[0].title or 'cancelled' in notifs[0].title

    def test_closure_notifies_assigned_walker(self, app, client):
        """Assigned walker must receive a booking_cancelled notification (§7.4)."""
        ids = self._setup(app)
        _login(client, ids['admin_email'])
        _post_closure(client, ids['monday'].isoformat())

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=ids['walker_uid']).all()
            assert len(notifs) == 1, "walker must get exactly one notification"
            assert notifs[0].notification_type == 'booking_cancelled'

    def test_closure_does_not_double_notify_admin_as_walker(self, app, client):
        """If the acting admin is also the assigned walker, they must NOT get a
        walker notification — they already know because they created the closure."""
        monday = _next_weekday(0)
        with app.app_context():
            # Admin IS the walker
            admin_u = _make_user('closure_adminwalker@test.com', role='walker', is_admin=True)
            admin_walker = Walker(user_id=admin_u.id)
            db.session.add(admin_walker); db.session.flush()
            primary = _make_user('closure_p2@test.com', role='client')
            db.session.add(Client(user_id=primary.id, onboarding_completed=True))
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned', slot_type='morning_afternoon',
                requires_walker=True, default_max_capacity=6, active=True,
            )
            db.session.add(st); db.session.flush()
            dog = Dog(name='Rex', breed='Terrier')
            db.session.add(dog); db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=primary.id, role='primary'))
            booking = Booking(
                user_id=primary.id, dog_id=dog.id, service_type_id=st.id,
                date=monday, slot='Morning', status='confirmed',
                walker_id=admin_walker.id,
            )
            db.session.add(booking); db.session.commit()
            admin_email = admin_u.email
            admin_uid = admin_u.id

        _login(client, admin_email)
        _post_closure(client, monday.isoformat())

        with app.app_context():
            # Admin/walker should get zero notifications (they created the closure)
            notifs = Notification.query.filter_by(recipient_id=admin_uid).all()
            assert len(notifs) == 0
