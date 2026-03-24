"""
Tests for multi-owner (shared dog access) feature — #27.

Covers:
- get_accessible_dog_ids / user_can_access_booking helpers
- Admin join_dog_access / revoke_dog_access routes
- Client cancel_booking auth for secondary owners
"""
import json
import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import User, Client, Dog, DogOwner, Walker, ServiceType, Booking
from app.utils.booking_access import get_accessible_dog_ids, user_can_access_booking
from tests.conftest import make_user, login


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client_user(firstname='Client', lastname='User', email=None):
    user = make_user(firstname=firstname, lastname=lastname, email=email, role='client')
    profile = Client(user_id=user.id, onboarding_completed=True)
    db.session.add(profile)
    db.session.flush()
    return user


def make_dog(name='Buddy'):
    dog = Dog(name=name, breed='Labrador')
    db.session.add(dog)
    db.session.flush()
    return dog


def make_primary_ownership(dog, user):
    o = DogOwner(dog_id=dog.id, user_id=user.id, role='primary')
    db.session.add(o)
    db.session.flush()
    return o


def make_secondary_ownership(dog, user):
    o = DogOwner(dog_id=dog.id, user_id=user.id, role='secondary')
    db.session.add(o)
    db.session.flush()
    return o


def make_service_type():
    st = ServiceType(
        name='Group Walk', slug='group-walk',
        capacity_model='walker_assigned', slot_type='morning_afternoon',
        requires_walker=True, default_max_capacity=6, active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


def make_booking(user, dog, service_type, date_str='2099-06-01', slot='Morning', status='confirmed'):
    from datetime import date
    b = Booking(
        user_id=user.id, dog_id=dog.id, service_type_id=service_type.id,
        date=date.fromisoformat(date_str), slot=slot, status=status,
    )
    db.session.add(b)
    db.session.flush()
    return b


def make_admin():
    user = make_user(firstname='Admin', lastname='One',
                     email='admin@testmulti.org', role='walker', is_admin=True)
    db.session.flush()
    return user


# ---------------------------------------------------------------------------
# get_accessible_dog_ids
# ---------------------------------------------------------------------------

class TestGetAccessibleDogIds:

    def test_primary_owner_sees_own_dog(self, app, db):
        with app.app_context():
            user = make_client_user(email='primary@test.org')
            dog = make_dog()
            make_primary_ownership(dog, user)
            db.session.commit()

            ids = get_accessible_dog_ids(user.id)
            assert dog.id in ids

    def test_secondary_owner_sees_shared_dog(self, app, db):
        with app.app_context():
            primary = make_client_user(email='prim2@test.org')
            secondary = make_client_user(email='sec2@test.org')
            dog = make_dog()
            make_primary_ownership(dog, primary)
            make_secondary_ownership(dog, secondary)
            db.session.commit()

            ids = get_accessible_dog_ids(secondary.id)
            assert dog.id in ids

    def test_unrelated_user_sees_no_dogs(self, app, db):
        with app.app_context():
            owner = make_client_user(email='owner3@test.org')
            stranger = make_client_user(email='stranger3@test.org')
            dog = make_dog()
            make_primary_ownership(dog, owner)
            db.session.commit()

            ids = get_accessible_dog_ids(stranger.id)
            assert dog.id not in ids

    def test_user_with_no_dogs_returns_empty(self, app, db):
        with app.app_context():
            user = make_client_user(email='nodogs@test.org')
            db.session.commit()
            assert get_accessible_dog_ids(user.id) == []


# ---------------------------------------------------------------------------
# user_can_access_booking
# ---------------------------------------------------------------------------

class TestUserCanAccessBooking:

    def test_booking_creator_can_access(self, app, db):
        with app.app_context():
            user = make_client_user(email='creator@test.org')
            dog = make_dog()
            make_primary_ownership(dog, user)
            st = make_service_type()
            booking = make_booking(user, dog, st)
            db.session.commit()

            assert user_can_access_booking(user, booking) is True

    def test_secondary_owner_can_access_primary_booking(self, app, db):
        with app.app_context():
            primary = make_client_user(email='prim4@test.org')
            secondary = make_client_user(email='sec4@test.org')
            dog = make_dog()
            make_primary_ownership(dog, primary)
            make_secondary_ownership(dog, secondary)
            st = make_service_type()
            # Booking was made by primary
            booking = make_booking(primary, dog, st)
            db.session.commit()

            assert user_can_access_booking(secondary, booking) is True

    def test_unrelated_user_cannot_access(self, app, db):
        with app.app_context():
            owner = make_client_user(email='owner5@test.org')
            stranger = make_client_user(email='stranger5@test.org')
            dog = make_dog()
            make_primary_ownership(dog, owner)
            st = make_service_type()
            booking = make_booking(owner, dog, st)
            db.session.commit()

            assert user_can_access_booking(stranger, booking) is False

    def test_admin_can_access_any_booking(self, app, db):
        with app.app_context():
            owner = make_client_user(email='owner6@test.org')
            admin = make_admin()
            dog = make_dog()
            make_primary_ownership(dog, owner)
            st = make_service_type()
            booking = make_booking(owner, dog, st)
            db.session.commit()

            assert user_can_access_booking(admin, booking) is True


# ---------------------------------------------------------------------------
# Admin join / revoke routes
# ---------------------------------------------------------------------------

class TestAdminJoinRevoke:

    def _setup(self, app):
        """Create admin, two clients each with a dog, and a service type."""
        admin = make_admin()
        primary = make_client_user(email='joinprimary@test.org')
        secondary = make_client_user(email='joinsecondary@test.org')
        dog = make_dog('Fudge')
        make_primary_ownership(dog, primary)
        dog2 = make_dog('Biscuit')
        make_primary_ownership(dog2, secondary)
        db.session.commit()
        return admin, primary, secondary, dog

    def test_join_grants_secondary_access(self, app, db, client):
        with app.app_context():
            admin, primary, secondary, dog = self._setup(app)
            login(client, admin.email)

            resp = client.post(
                f'/admin/clients/{primary.id}/join',
                data=json.dumps({'dog_id': dog.id, 'secondary_user_id': secondary.id}),
                content_type='application/json',
            )
            data = resp.get_json()
            assert resp.status_code == 200
            assert data['success'] is True

            record = DogOwner.query.filter_by(
                dog_id=dog.id, user_id=secondary.id, role='secondary'
            ).first()
            assert record is not None

    def test_join_duplicate_returns_409(self, app, db, client):
        with app.app_context():
            admin, primary, secondary, dog = self._setup(app)
            make_secondary_ownership(dog, secondary)
            db.session.commit()
            login(client, admin.email)

            resp = client.post(
                f'/admin/clients/{primary.id}/join',
                data=json.dumps({'dog_id': dog.id, 'secondary_user_id': secondary.id}),
                content_type='application/json',
            )
            assert resp.status_code == 409

    def test_join_self_returns_400(self, app, db, client):
        with app.app_context():
            admin, primary, secondary, dog = self._setup(app)
            login(client, admin.email)

            resp = client.post(
                f'/admin/clients/{primary.id}/join',
                data=json.dumps({'dog_id': dog.id, 'secondary_user_id': primary.id}),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_join_wrong_dog_returns_404(self, app, db, client):
        with app.app_context():
            admin, primary, secondary, dog = self._setup(app)
            login(client, admin.email)
            # dog2 belongs to secondary, not primary
            dog2 = DogOwner.query.filter_by(user_id=secondary.id, role='primary').first()

            resp = client.post(
                f'/admin/clients/{primary.id}/join',
                data=json.dumps({'dog_id': dog2.dog_id, 'secondary_user_id': secondary.id}),
                content_type='application/json',
            )
            assert resp.status_code == 404

    def test_revoke_removes_secondary_record(self, app, db, client):
        with app.app_context():
            admin, primary, secondary, dog = self._setup(app)
            make_secondary_ownership(dog, secondary)
            db.session.commit()
            login(client, admin.email)

            resp = client.post(
                f'/admin/clients/{primary.id}/revoke-access',
                data=json.dumps({'dog_id': dog.id, 'secondary_user_id': secondary.id}),
                content_type='application/json',
            )
            data = resp.get_json()
            assert resp.status_code == 200
            assert data['success'] is True

            record = DogOwner.query.filter_by(
                dog_id=dog.id, user_id=secondary.id, role='secondary'
            ).first()
            assert record is None

    def test_revoke_nonexistent_returns_404(self, app, db, client):
        with app.app_context():
            admin, primary, secondary, dog = self._setup(app)
            login(client, admin.email)

            resp = client.post(
                f'/admin/clients/{primary.id}/revoke-access',
                data=json.dumps({'dog_id': dog.id, 'secondary_user_id': secondary.id}),
                content_type='application/json',
            )
            assert resp.status_code == 404

    def test_non_admin_cannot_join(self, app, db, client):
        with app.app_context():
            admin, primary, secondary, dog = self._setup(app)
            login(client, primary.email)  # log in as client, not admin

            resp = client.post(
                f'/admin/clients/{primary.id}/join',
                data=json.dumps({'dog_id': dog.id, 'secondary_user_id': secondary.id}),
                content_type='application/json',
            )
            assert resp.status_code in (302, 403)  # redirect to login or explicit 403


# ---------------------------------------------------------------------------
# Secondary owner can cancel a booking they didn't create
# ---------------------------------------------------------------------------

class TestSecondaryOwnerCancelBooking:

    def test_secondary_can_cancel_primary_booking(self, app, db, client):
        with app.app_context():
            primary = make_client_user(email='cancelprim@test.org')
            secondary = make_client_user(email='cancelsec@test.org')
            dog = make_dog('Peanut')
            make_primary_ownership(dog, primary)
            make_secondary_ownership(dog, secondary)
            st = make_service_type()
            booking = make_booking(primary, dog, st)
            db.session.commit()
            booking_id = booking.id

            login(client, secondary.email)
            resp = client.post(
                '/cancel_booking',
                data={'booking_id': booking_id},
            )
            data = resp.get_json()
            assert data['success'] is True

            updated = db.session.get(Booking, booking_id)
            assert updated.status == 'cancelled'

    def test_unrelated_client_cannot_cancel(self, app, db, client):
        with app.app_context():
            owner = make_client_user(email='owner_nc@test.org')
            stranger = make_client_user(email='stranger_nc@test.org')
            dog = make_dog('Truffle')
            make_primary_ownership(dog, owner)
            st = make_service_type()
            booking = make_booking(owner, dog, st)
            db.session.commit()
            booking_id = booking.id

            login(client, stranger.email)
            resp = client.post(
                '/cancel_booking',
                data={'booking_id': booking_id},
            )
            data = resp.get_json()
            assert data['success'] is False
            assert resp.status_code == 403
