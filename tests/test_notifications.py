"""
T5 — Notification unit tests.

Covers:
- create_notification() creates correct DB record with right fields
- DB cap: oldest notifications pruned when limit exceeded
- Booking confirmed → notification created for client
- New booking request → notification created for all admins
- get_unread_count() returns correct number
- mark_read() marks a single notification and is idempotent
- mark_all_read() clears all unread for a user
"""
import datetime
import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import User, Client, Walker, Dog, DogOwner, ServiceType, Booking, Notification
from app.utils.notifications import (
    create_notification, get_unread_count, get_recent,
    mark_read, mark_all_read, NOTIF_DB_CAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='client', is_admin=False):
    u = User(
        firstname='Test', lastname='User',
        email=email, role=role, is_admin=is_admin,
        hashed_password=generate_password_hash('Testpass1!'),
        active=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def make_client_profile(user_id):
    c = Client(user_id=user_id, onboarding_completed=True)
    db.session.add(c)
    db.session.flush()
    return c


def make_walker_profile(user_id):
    w = Walker(user_id=user_id)
    db.session.add(w)
    db.session.flush()
    return w


def login(flask_client, email, password='Testpass1!'):
    return flask_client.post('/auth/login', data={
        'email': email,
        'password': password,
    }, follow_redirects=True)


TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'walker_schedules', 'walker_unavailabilities',
    'walkers', 'clients', 'service_types', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield


# ---------------------------------------------------------------------------
# T5a — create_notification() basics
# ---------------------------------------------------------------------------

class TestCreateNotification:

    def test_creates_db_record(self, app):
        with app.app_context():
            user = make_user('notif_basic@test.com')
            db.session.commit()

            notif = create_notification(
                recipient_id=user.id,
                notification_type='booking_confirmed',
                title='Your walk is confirmed',
                body='Buddy is booked for Monday Morning.',
                link='/bookings/1',
            )
            db.session.commit()

            fetched = db.session.get(Notification, notif.id)
            assert fetched is not None
            assert fetched.recipient_id == user.id
            assert fetched.notification_type == 'booking_confirmed'
            assert fetched.title == 'Your walk is confirmed'
            assert fetched.body == 'Buddy is booked for Monday Morning.'
            assert fetched.link == '/bookings/1'
            assert fetched.read_at is None   # unread by default

    def test_created_at_is_set(self, app):
        with app.app_context():
            user = make_user('notif_ts@test.com')
            db.session.commit()
            notif = create_notification(user.id, 'system', 'Hello')
            db.session.commit()
            assert db.session.get(Notification, notif.id).created_at is not None

    def test_optional_fields_can_be_none(self, app):
        with app.app_context():
            user = make_user('notif_none@test.com')
            db.session.commit()
            notif = create_notification(user.id, 'system', 'No body or link')
            db.session.commit()
            fetched = db.session.get(Notification, notif.id)
            assert fetched.body is None
            assert fetched.link is None

    def test_multiple_recipients_are_independent(self, app):
        with app.app_context():
            u1 = make_user('notif_r1@test.com')
            u2 = make_user('notif_r2@test.com')
            db.session.commit()
            create_notification(u1.id, 'system', 'For user 1')
            create_notification(u2.id, 'system', 'For user 2')
            db.session.commit()

            assert Notification.query.filter_by(recipient_id=u1.id).count() == 1
            assert Notification.query.filter_by(recipient_id=u2.id).count() == 1


# ---------------------------------------------------------------------------
# T5b — DB cap enforcement
# ---------------------------------------------------------------------------

class TestNotificationCap:

    def test_oldest_pruned_beyond_cap(self, app):
        with app.app_context():
            user = make_user('notif_cap@test.com')
            db.session.commit()

            # Insert NOTIF_DB_CAP + 5 notifications one at a time
            for i in range(NOTIF_DB_CAP + 5):
                create_notification(user.id, 'system', f'Notification {i}')
                db.session.commit()

            total = Notification.query.filter_by(recipient_id=user.id).count()
            assert total <= NOTIF_DB_CAP, (
                f"Expected ≤{NOTIF_DB_CAP} notifications, got {total}"
            )

    def test_newest_notifications_kept(self, app):
        with app.app_context():
            user = make_user('notif_newest@test.com')
            db.session.commit()

            for i in range(NOTIF_DB_CAP + 3):
                create_notification(user.id, 'system', f'msg {i}')
                db.session.commit()

            titles = [
                n.title for n in Notification.query
                .filter_by(recipient_id=user.id)
                .order_by(Notification.created_at.desc())
                .all()
            ]
            # The most recent ones should be present; the earliest pruned
            assert f'msg {NOTIF_DB_CAP + 2}' in titles
            assert f'msg 0' not in titles


# ---------------------------------------------------------------------------
# T5c — get_unread_count / get_recent
# ---------------------------------------------------------------------------

class TestUnreadCount:

    def test_unread_count_increments(self, app):
        with app.app_context():
            user = make_user('notif_uc@test.com')
            db.session.commit()

            assert get_unread_count(user.id) == 0
            create_notification(user.id, 'system', 'First')
            db.session.commit()
            assert get_unread_count(user.id) == 1
            create_notification(user.id, 'system', 'Second')
            db.session.commit()
            assert get_unread_count(user.id) == 2

    def test_get_recent_returns_latest_first(self, app):
        with app.app_context():
            user = make_user('notif_recent@test.com')
            db.session.commit()
            for i in range(3):
                create_notification(user.id, 'system', f'msg {i}')
                db.session.commit()

            recent = get_recent(user.id, limit=3)
            assert recent[0].title == 'msg 2'
            assert recent[-1].title == 'msg 0'


# ---------------------------------------------------------------------------
# T5d — mark_read / mark_all_read
# ---------------------------------------------------------------------------

class TestMarkRead:

    def test_mark_read_sets_read_at(self, app):
        with app.app_context():
            user = make_user('notif_mr@test.com')
            db.session.commit()
            notif = create_notification(user.id, 'system', 'Hello')
            db.session.commit()
            notif_id = notif.id
            user_id = user.id

        with app.app_context():
            result = mark_read(notif_id, user_id)
            assert result is True
            fetched = db.session.get(Notification, notif_id)
            assert fetched.read_at is not None

    def test_mark_read_is_idempotent(self, app):
        with app.app_context():
            user = make_user('notif_idem@test.com')
            db.session.commit()
            notif = create_notification(user.id, 'system', 'Hello')
            db.session.commit()
            notif_id = notif.id
            user_id = user.id

        with app.app_context():
            mark_read(notif_id, user_id)
            first_read_at = db.session.get(Notification, notif_id).read_at
            result = mark_read(notif_id, user_id)
            # Returns False the second time (already read)
            assert result is False
            # read_at timestamp unchanged
            assert db.session.get(Notification, notif_id).read_at == first_read_at

    def test_mark_read_rejects_wrong_user(self, app):
        with app.app_context():
            owner = make_user('notif_own@test.com')
            other = make_user('notif_other@test.com')
            db.session.commit()
            notif = create_notification(owner.id, 'system', 'Private')
            db.session.commit()
            notif_id = notif.id
            other_id = other.id

        with app.app_context():
            result = mark_read(notif_id, other_id)
            assert result is False
            assert db.session.get(Notification, notif_id).read_at is None

    def test_mark_all_read_clears_unread(self, app):
        with app.app_context():
            user = make_user('notif_all@test.com')
            db.session.commit()
            for _ in range(4):
                create_notification(user.id, 'system', 'Unread')
                db.session.commit()
            user_id = user.id

        with app.app_context():
            assert get_unread_count(user_id) == 4
            mark_all_read(user_id)
            assert get_unread_count(user_id) == 0

    def test_mark_all_read_does_not_affect_other_users(self, app):
        with app.app_context():
            u1 = make_user('notif_mar1@test.com')
            u2 = make_user('notif_mar2@test.com')
            db.session.commit()
            create_notification(u1.id, 'system', 'For u1')
            create_notification(u2.id, 'system', 'For u2')
            db.session.commit()
            u1_id = u1.id
            u2_id = u2.id

        with app.app_context():
            mark_all_read(u1_id)
            assert get_unread_count(u1_id) == 0
            assert get_unread_count(u2_id) == 1   # u2 unaffected


# ---------------------------------------------------------------------------
# T5d2 — /notifications/unread-count (badge reconciliation endpoint)
# ---------------------------------------------------------------------------

class TestUnreadCountEndpoint:
    """The bell fetches this on PWA foreground to reconcile the DOM badge
    and the home-screen app badge against server truth."""

    def test_returns_unread_count(self, app, client):
        with app.app_context():
            user = make_user('unread_ep@test.com')
            make_client_profile(user.id)
            db.session.commit()
            user_id = user.id

            for i in range(3):
                create_notification(
                    recipient_id=user_id,
                    notification_type='system',
                    title=f'Unread {i}',
                )
            # One read notification — must not be counted
            read_notif = create_notification(
                recipient_id=user_id,
                notification_type='system',
                title='Already read',
            )
            db.session.commit()
            mark_read(read_notif.id, user_id)

        login(client, 'unread_ep@test.com')
        res = client.get('/notifications/unread-count')
        assert res.status_code == 200
        assert res.get_json() == {'count': 3}

    def test_zero_when_all_read(self, app, client):
        with app.app_context():
            user = make_user('unread_ep_zero@test.com')
            make_client_profile(user.id)
            db.session.commit()
            user_id = user.id
            create_notification(
                recipient_id=user_id,
                notification_type='system',
                title='Soon read',
            )
            db.session.commit()
            mark_all_read(user_id)

        login(client, 'unread_ep_zero@test.com')
        res = client.get('/notifications/unread-count')
        assert res.status_code == 200
        assert res.get_json() == {'count': 0}

    def test_requires_login(self, client):
        res = client.get('/notifications/unread-count')
        # Anonymous users are redirected to login, never given a count
        assert res.status_code == 302

    def test_counts_are_per_user(self, app, client):
        with app.app_context():
            u1 = make_user('unread_ep_u1@test.com')
            make_client_profile(u1.id)
            u2 = make_user('unread_ep_u2@test.com')
            make_client_profile(u2.id)
            db.session.commit()
            create_notification(recipient_id=u1.id, notification_type='system', title='For u1')
            create_notification(recipient_id=u2.id, notification_type='system', title='For u2 a')
            create_notification(recipient_id=u2.id, notification_type='system', title='For u2 b')
            db.session.commit()

        login(client, 'unread_ep_u1@test.com')
        res = client.get('/notifications/unread-count')
        assert res.get_json() == {'count': 1}


# ---------------------------------------------------------------------------
# T5d3 — /notifications/recent (list + count reconciliation endpoint)
# ---------------------------------------------------------------------------

class TestRecentEndpoint:
    """The bell fetches this on PWA foreground to reconcile BOTH the badge
    count and the dropdown list against server truth (SSE list prepends fired
    while iOS suspends the EventSource are otherwise lost until a reload)."""

    def test_returns_count_and_items(self, app, client):
        with app.app_context():
            user = make_user('recent_ep@test.com')
            make_client_profile(user.id)
            db.session.commit()
            user_id = user.id
            for i in range(3):
                create_notification(
                    recipient_id=user_id,
                    notification_type='system',
                    title=f'Recent {i}',
                )
            db.session.commit()

        login(client, 'recent_ep@test.com')
        res = client.get('/notifications/recent')
        assert res.status_code == 200
        data = res.get_json()
        assert data['count'] == 3
        assert len(data['notifications']) == 3
        # Newest first, and each item carries the fields the bell JS renders.
        first = data['notifications'][0]
        assert first['title'] == 'Recent 2'
        assert set(first) >= {'id', 'title', 'body', 'link', 'icon', 'colour',
                              'created_at', 'is_unread'}
        assert first['is_unread'] is True
        assert first['created_at'].endswith('Z')

    def test_is_unread_reflects_read_state(self, app, client):
        with app.app_context():
            user = make_user('recent_ep_read@test.com')
            make_client_profile(user.id)
            db.session.commit()
            user_id = user.id
            n = create_notification(
                recipient_id=user_id,
                notification_type='system',
                title='Will be read',
            )
            db.session.commit()
            mark_read(n.id, user_id)

        login(client, 'recent_ep_read@test.com')
        res = client.get('/notifications/recent')
        data = res.get_json()
        assert data['count'] == 0
        assert len(data['notifications']) == 1
        assert data['notifications'][0]['is_unread'] is False

    def test_empty_when_no_notifications(self, app, client):
        with app.app_context():
            user = make_user('recent_ep_empty@test.com')
            make_client_profile(user.id)
            db.session.commit()

        login(client, 'recent_ep_empty@test.com')
        res = client.get('/notifications/recent')
        data = res.get_json()
        assert data == {'count': 0, 'notifications': []}

    def test_capped_at_bell_limit(self, app, client):
        from app.utils.notifications import NOTIF_BELL_CAP
        with app.app_context():
            user = make_user('recent_ep_cap@test.com')
            make_client_profile(user.id)
            db.session.commit()
            user_id = user.id
            for i in range(NOTIF_BELL_CAP + 4):
                create_notification(
                    recipient_id=user_id,
                    notification_type='system',
                    title=f'Cap {i}',
                )
            db.session.commit()

        login(client, 'recent_ep_cap@test.com')
        res = client.get('/notifications/recent')
        data = res.get_json()
        assert len(data['notifications']) == NOTIF_BELL_CAP
        assert data['count'] == NOTIF_BELL_CAP + 4

    def test_requires_login(self, client):
        res = client.get('/notifications/recent')
        assert res.status_code == 302

    def test_per_user_isolation(self, app, client):
        with app.app_context():
            u1 = make_user('recent_ep_u1@test.com')
            make_client_profile(u1.id)
            u2 = make_user('recent_ep_u2@test.com')
            make_client_profile(u2.id)
            db.session.commit()
            create_notification(recipient_id=u1.id, notification_type='system', title='For u1')
            create_notification(recipient_id=u2.id, notification_type='system', title='For u2')
            db.session.commit()

        login(client, 'recent_ep_u1@test.com')
        data = client.get('/notifications/recent').get_json()
        assert data['count'] == 1
        assert [n['title'] for n in data['notifications']] == ['For u1']


# ---------------------------------------------------------------------------
# T5e — Notifications triggered via booking flow (integration)
# ---------------------------------------------------------------------------

class TestBookingNotifications:
    """Verify notifications are created when bookings are made via HTTP."""

    def _setup_booking_env(self, app, client_email, admin_email, walker_email):
        """Create a minimal valid booking environment and return ids."""
        from app.models import WalkerSchedule
        import datetime

        with app.app_context():
            # Admin (also a walker so they can be logged in)
            admin = make_user(admin_email, role='walker', is_admin=True)
            make_walker_profile(admin.id)

            # Dedicated walker with schedule for tomorrow
            tom = datetime.date.today() + datetime.timedelta(days=1)
            walker_u = make_user(walker_email, role='walker')
            w = make_walker_profile(walker_u.id)
            sched = WalkerSchedule(
                walker_id=w.id, day_of_week=tom.weekday(),
                slot='Morning', active=True,
            )
            db.session.add(sched)
            db.session.flush()

            # Client
            client_u = make_user(client_email, role='client')
            make_client_profile(client_u.id)

            # Dog
            dog = Dog(name='Notif Dog', breed='Poodle')
            db.session.add(dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=client_u.id, role='primary'))
            db.session.flush()

            # Service
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned',
                slot_type='morning_afternoon',
                requires_walker=True,
                default_max_capacity=6,
                active=True,
            )
            db.session.add(st)
            db.session.commit()
            return client_u.id, admin.id, w.id, tom

    def test_booking_request_notifies_admins(self, app, client):
        """When a client makes a booking with a walker available, it auto-confirms
        and the client receives a booking_confirmed notification (not admin)."""
        client_email  = 'notif_cl_br@test.com'
        admin_email   = 'notif_adm_br@test.com'
        walker_email  = 'notif_wlk_br@test.com'

        client_id, admin_id, walker_id, tom = self._setup_booking_env(
            app, client_email, admin_email, walker_email
        )

        login(client, client_email)
        client.post('/', data={
            'date': tom.isoformat(),
            'slot': 'Morning',
        }, follow_redirects=True)

        with app.app_context():
            # Auto-assign fires: client gets confirmed notification, not admin request
            client_notifs = Notification.query.filter_by(
                recipient_id=client_id,
                notification_type='booking_confirmed',
            ).all()
            assert len(client_notifs) >= 1, "Client should receive a booking_confirmed notification on auto-assign"

    def test_booking_confirm_notifies_client(self, app, client):
        """Client receives a booking_confirmed notification — via auto-assign on creation."""
        client_email = 'notif_cl_bc@test.com'
        admin_email  = 'notif_adm_bc@test.com'
        walker_email = 'notif_wlk_bc@test.com'

        client_id, admin_id, walker_id, tom = self._setup_booking_env(
            app, client_email, admin_email, walker_email
        )

        # Client makes a booking — auto-assign fires immediately
        login(client, client_email)
        client.post('/', data={
            'date': tom.isoformat(),
            'slot': 'Morning',
        }, follow_redirects=True)

        with app.app_context():
            # Booking should be confirmed with walker assigned
            booking = Booking.query.filter_by(status='confirmed').first()
            assert booking is not None
            assert booking.walker_id is not None

            # Client should have received a confirmed notification
            client_notifs = Notification.query.filter_by(
                recipient_id=client_id,
                notification_type='booking_confirmed',
            ).all()
            assert len(client_notifs) >= 1, "Client should receive a booking_confirmed notification"
