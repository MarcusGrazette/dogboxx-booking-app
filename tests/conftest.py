"""
Pytest configuration and shared fixtures for Dogboxx test suite.

All tests use an in-memory SQLite database with a fresh schema per test.
CSRF is disabled in test config. Flask-Limiter is disabled in tests.
"""
import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db as _db
from app.models import User, Client, Dog, DogOwner, Walker, ServiceType, Booking


# ---------------------------------------------------------------------------
# App / DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def app():
    """Create application with testing config (in-memory SQLite)."""
    application = create_app('testing')
    # Disable rate limiter in tests
    application.config['RATELIMIT_ENABLED'] = False
    return application


@pytest.fixture(autouse=True)
def db(app):
    """
    Create all tables before each test, drop them after.
    Reliable isolation across SQLAlchemy 2.x — slightly slower than
    transaction rollback but avoids session.bind deprecation issues.
    """
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# User / role helpers
# ---------------------------------------------------------------------------

def make_user(firstname='Test', lastname='User', email=None, role='client',
              is_admin=False, password='Testpass1!'):
    """Create and persist a User. Returns the User instance.

    Commits the session so the row is durable across HTTP request
    boundaries — fixtures consumed by the logged_in_* helpers must be
    visible to the test client's request connection, which on Postgres
    uses its own session and won't see uncommitted rows."""
    if email is None:
        import uuid
        email = f'test_{uuid.uuid4().hex[:8]}@example.com'
    user = User(
        firstname=firstname,
        lastname=lastname,
        email=email,
        role=role,
        is_admin=is_admin,
        hashed_password=generate_password_hash(password),
    )
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture
def admin_user():
    """A user with is_admin=True and role=walker (mirrors production pattern)."""
    return make_user(firstname='Admin', lastname='User',
                     email='admin@test.dogboxx.org',
                     role='walker', is_admin=True)


@pytest.fixture
def walker_user():
    """A plain walker user with a linked Walker profile."""
    user = make_user(firstname='Walker', lastname='One',
                     email='walker@test.dogboxx.org', role='walker')
    walker = Walker(user_id=user.id)
    _db.session.add(walker)
    _db.session.commit()
    return user


@pytest.fixture
def client_user():
    """A plain client user with a linked Client profile (onboarding complete)."""
    user = make_user(firstname='Client', lastname='One',
                     email='client@test.dogboxx.org', role='client')
    profile = Client(user_id=user.id, onboarding_completed=True)
    _db.session.add(profile)
    _db.session.commit()
    return user


@pytest.fixture
def dog(client_user):
    """A dog owned by the client_user fixture."""
    dog = Dog(name='Buddy', breed='Labrador')
    _db.session.add(dog)
    _db.session.flush()
    assoc = DogOwner(dog_id=dog.id, user_id=client_user.id, role='primary')
    _db.session.add(assoc)
    _db.session.commit()
    return dog


@pytest.fixture
def service_type():
    """A basic group-walk ServiceType."""
    st = ServiceType(
        name='Group Walk',
        slug='group-walk',
        capacity_model='walker_assigned',
        slot_type='morning_afternoon',
        requires_walker=True,
        default_max_capacity=6,
        active=True,
    )
    _db.session.add(st)
    _db.session.commit()
    return st


# ---------------------------------------------------------------------------
# Authenticated test client helpers
# ---------------------------------------------------------------------------

def login(flask_client, email, password='Testpass1!'):
    """POST to /auth/login and return the test client (session retained)."""
    return flask_client.post('/auth/login', data={
        'email': email,
        'password': password,
    }, follow_redirects=True)


@pytest.fixture
def logged_in_admin(app, admin_user, client):
    """Test client pre-logged-in as admin."""
    with app.app_context():
        login(client, admin_user.email)
    return client


@pytest.fixture
def logged_in_client(app, client_user, client):
    """Test client pre-logged-in as client."""
    with app.app_context():
        login(client, client_user.email)
    return client


@pytest.fixture
def logged_in_walker(app, walker_user, client):
    """Test client pre-logged-in as walker."""
    with app.app_context():
        login(client, walker_user.email)
    return client
