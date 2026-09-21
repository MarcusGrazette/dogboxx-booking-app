"""Transaction integrity — PR 1 of the 2026-09-09 develop review (findings #1, #12).

The bug these guard against is not in any one route. Flask-Session stores sessions
in the app's own db.session (`SESSION_SQLALCHEMY = db` in app/__init__.py), and its
`_upsert_session()` ends with `db.session.commit()`. Flask's
`SESSION_REFRESH_EACH_REQUEST` defaults to True, so that commit runs on essentially
every request from a logged-in user — *after* the view returned, *before* the
scoped session is removed.

Consequences, each covered below:
  #1  `return jsonify(...), 400` without a rollback is a deferred COMMIT, not an
      escape hatch. Three live routes leaked state this way.
  #12 `session.info` is scoped to the Session object, not the transaction, so the
      queued SSE/Web-Push payloads survived a rollback and were delivered by the
      next commit — which the stray commit above reliably supplied.

**These tests must assert on PERSISTED STATE, not on the response.** Every one of
these routes already returned the correct error code; that is exactly why the bug
survived. A test that stops at `assert resp.status_code == 400` proves nothing.
"""
import contextlib
import datetime
import io
import logging

import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    User, Client, Dog, DogOwner, Walker, WalkerSchedule, ServiceType, Booking,
)

TRUNCATE_ORDER = [
    'activity_logs', 'booking_status_changes', 'bookings', 'notifications',
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


def _weekday_soon():
    """Next Mon-Fri date at least a day out — DogBoxx doesn't operate weekends."""
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() > 4:
        d += datetime.timedelta(days=1)
    return d


def _make_user(email, role='client', is_admin=False):
    u = User(firstname='Test', lastname='User', email=email, role=role,
             is_admin=is_admin, active=True,
             hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.flush()
    return u


def _make_service(slug='group-walk', name='Group Walk', capacity=6):
    st = ServiceType(name=name, slug=slug, capacity_model='walker_assigned',
                     slot_type='morning_afternoon', requires_walker=True,
                     default_max_capacity=capacity, active=True)
    db.session.add(st)
    db.session.flush()
    return st


def _make_walker(email, day_of_week, slot):
    u = _make_user(email, role='walker')
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.flush()
    db.session.add(WalkerSchedule(walker_id=w.id, day_of_week=day_of_week,
                                  slot=slot, active=True))
    db.session.flush()
    return w


def _login(flask_client, email, password='Testpass1!'):
    return flask_client.post('/auth/login', data={'email': email, 'password': password},
                             follow_redirects=True)


@contextlib.contextmanager
def _capture_app_log(app):
    """Collect messages written to app.logger.

    caplog doesn't see these reliably — configure_logging() attaches its own
    handlers to app.logger and the guard logs through that, not the root logger.
    """
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    handler.setLevel(logging.ERROR)
    app.logger.addHandler(handler)
    try:
        yield records
    finally:
        app.logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# #1a — admin "Both" booking must not half-succeed
# ---------------------------------------------------------------------------

class TestAdminBothBookingIsAllOrNothing:
    """Reproduces the headline case from the review.

    A Morning-only walker means the Afternoon leg has no capacity. Before the
    fix the loop created Morning (which flushes an INSERT), then raised
    CapacityError on Afternoon and returned 400 — and the Morning booking
    persisted, with a walker assigned and a pickup_order set, while the
    NotificationBatch (built after the loop) never ran. The admin was told
    nothing happened; the client was never told anything at all.
    """

    def _setup(self, app):
        day = _weekday_soon()
        with app.app_context():
            _make_walker(f'w_am_{id(self)}@t.com', day.weekday(), 'Morning')
            _make_service()
            admin = _make_user(f'admin_{id(self)}@t.com', role='walker', is_admin=True)
            owner = _make_user(f'owner_{id(self)}@t.com')
            db.session.add(Client(user_id=owner.id, onboarding_completed=True))
            dog = Dog(name='Buddy', breed='Labrador')
            db.session.add(dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=owner.id, role='primary'))
            db.session.commit()
            return admin.email, owner.id, dog.id, day

    def test_afternoon_failure_persists_no_morning_booking(self, app, client):
        admin_email, owner_id, dog_id, day = self._setup(app)
        _login(client, admin_email)

        resp = client.post('/admin/book_for_dog', json={
            'dog_id': dog_id, 'user_id': owner_id,
            'date': day.isoformat(), 'slot': 'Both',
        })

        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

        with app.app_context():
            rows = Booking.query.filter_by(dog_id=dog_id, date=day).all()
            assert rows == [], (
                'Partial booking persisted despite the 400: '
                f'{[(b.slot, b.status) for b in rows]}'
            )

    def test_both_slots_available_still_books_both(self, app, client):
        """Guard against over-correcting: the happy path must be unaffected."""
        admin_email, owner_id, dog_id, day = self._setup(app)
        with app.app_context():
            _make_walker(f'w_pm_{id(self)}@t.com', day.weekday(), 'Afternoon')
            db.session.commit()

        _login(client, admin_email)
        resp = client.post('/admin/book_for_dog', json={
            'dog_id': dog_id, 'user_id': owner_id,
            'date': day.isoformat(), 'slot': 'Both',
        })

        assert resp.status_code == 200, resp.get_data(as_text=True)
        with app.app_context():
            slots = sorted(b.slot for b in Booking.query.filter_by(dog_id=dog_id, date=day))
            assert slots == ['Afternoon', 'Morning']


# ---------------------------------------------------------------------------
# #1b — onboarding must not mark the client onboarded when the photo is rejected
# ---------------------------------------------------------------------------

class TestOnboardingPhotoFailureLeavesNoState:
    """Note this path returns HTTP 200 (flash + render_template).

    That matters beyond this one route: it rules out "roll back on 4xx/5xx" as a
    general guard, which is why the response-time guard below triggers on session
    dirtiness rather than on status code.
    """

    def _setup(self, app):
        with app.app_context():
            u = _make_user(f'onboard_{id(self)}@t.com')
            db.session.add(Client(user_id=u.id, onboarding_completed=False))
            db.session.commit()
            return u.email, u.id

    def test_bad_photo_does_not_persist_onboarding_completed(self, app, client):
        email, user_id = self._setup(app)
        _login(client, email)

        # A .txt masquerading as an upload — process_dog_photo raises ValueError.
        resp = client.post('/onboard', data={
            'address_line_1': '1 Test Street',
            'postcode': 'SW1A 1AA',
            'dog_name': 'Buddy',
            'dog_gender': 'male',
            'file': (io.BytesIO(b'not an image'), 'notes.txt'),
        }, content_type='multipart/form-data', follow_redirects=False)

        # Renders the form back with a flashed error — deliberately NOT a 4xx.
        assert resp.status_code == 200

        with app.app_context():
            c = Client.query.filter_by(user_id=user_id).first()
            assert c.onboarding_completed is False, (
                'onboarding_completed persisted even though the upload was rejected'
            )
            assert c.street_address is None, 'address persisted on a failed submit'
            assert Dog.query.count() == 0, 'a dog was created despite the error'


# ---------------------------------------------------------------------------
# #1c — pickup-details save must not persist maps_url when it 404s
# ---------------------------------------------------------------------------

class TestPickupDetailsNoDogLeavesNoState:
    """A client with no dog is a normal state (/admin/clients/new needs only
    name + email). The 404 used to come *after* `client.maps_url = maps_url`,
    so the admin was told the save failed while the URL changed anyway — and
    record_admin_action never ran, so the change also escaped the ActivityLog
    chokepoint with no audit row.
    """

    def _setup(self, app):
        with app.app_context():
            admin = _make_user(f'adm_pickup_{id(self)}@t.com', role='walker', is_admin=True)
            target = _make_user(f'nodog_{id(self)}@t.com')
            db.session.add(Client(user_id=target.id, onboarding_completed=True,
                                  maps_url='https://maps.example/original'))
            db.session.commit()
            return admin.email, target.id

    def test_404_does_not_persist_maps_url(self, app, client):
        admin_email, target_id = self._setup(app)
        _login(client, admin_email)

        resp = client.post(f'/admin/clients/{target_id}/pickup-details', json={
            'maps_url': 'https://maps.example/CHANGED',
            'pickup_instructions': '<p>Key safe code 1234</p>',
        })

        assert resp.status_code == 404
        with app.app_context():
            c = Client.query.filter_by(user_id=target_id).first()
            assert c.maps_url == 'https://maps.example/original', (
                'maps_url persisted despite the 404 response'
            )
            from app.models import ActivityLog
            assert ActivityLog.query.count() == 0, (
                'an audit row was written for a change that was reported as failed'
            )


# ---------------------------------------------------------------------------
# #12 — a rolled-back notification must never be delivered
# ---------------------------------------------------------------------------

class TestRolledBackNotificationsAreDiscarded:

    def test_rollback_then_unrelated_commit_delivers_nothing(self, app, monkeypatch):
        """create_notification → rollback → an unrelated commit on the same session.

        Before the fix the queues in session.info survived the rollback and the
        next commit drained them. In a real request that next commit is
        Flask-Session's own, so delivery was near-certain.
        """
        import app.sse as sse_mod
        from app.utils.notifications import create_notification

        sent = []
        monkeypatch.setattr(sse_mod, 'broadcast',
                            lambda uid, ev, data: sent.append((uid, ev)))

        with app.app_context():
            recipient = _make_user(f'rb_recip_{id(self)}@t.com')
            other = _make_user(f'rb_other_{id(self)}@t.com')
            db.session.commit()

            create_notification(
                recipient_id=recipient.id,
                notification_type='system',
                title='should never be delivered',
                link='/',
            )
            assert db.session.info.get('sse_pending'), 'nothing was queued — test is vacuous'

            db.session.rollback()
            assert not db.session.info.get('sse_pending'), \
                'rollback left the SSE queue populated'
            assert not db.session.info.get('webpush_pending'), \
                'rollback left the Web Push queue populated'

            # Any later commit on the same session — in production this is
            # Flask-Session's response-time write.
            db.session.get(User, other.id).lastname = 'Unrelated'
            db.session.commit()

        assert sent == [], f'a rolled-back notification was still broadcast: {sent}'

    def test_committed_notification_is_still_delivered(self, app, monkeypatch):
        """The discard must not break the happy path."""
        import app.sse as sse_mod
        from app.utils.notifications import create_notification

        sent = []
        monkeypatch.setattr(sse_mod, 'broadcast',
                            lambda uid, ev, data: sent.append((uid, ev)))

        with app.app_context():
            recipient = _make_user(f'ok_recip_{id(self)}@t.com')
            db.session.commit()
            recipient_id = recipient.id   # read inside the context; the instance
                                          # detaches when it closes
            create_notification(recipient_id=recipient_id, notification_type='system',
                                title='should be delivered', link='/')
            db.session.commit()

        assert sent == [(recipient_id, 'notification')], \
            f'expected exactly one broadcast, got {sent}'


# ---------------------------------------------------------------------------
# The response-time guard (enforcing since 2026-09-21, FEATURES.md #77)
# ---------------------------------------------------------------------------

class TestUncommittedSessionGuard:
    """ENFORCING as of 2026-09-21 (FEATURES.md #77) — a clean week in prod with
    zero hits proved the accompanying route fixes found every leak site at the
    time. These tests pin: it must fire *and roll back* when work is left
    pending, and must stay silent on ordinary requests.

    A noisy guard would be worse than none: Sentry's LoggingIntegration turns
    each hit into an event, so a false positive pages the owner.
    """

    def test_guard_logs_when_a_route_leaves_the_session_dirty(
            self, app, client, dirty_session_route):
        with app.app_context():
            u = _make_user(f'guard_dirty_{id(self)}@t.com')
            # Without a Client record a before_request gate redirects to
            # /account-pending and the route under test never runs.
            db.session.add(Client(user_id=u.id, onboarding_completed=True))
            db.session.commit()
            email, user_id = u.email, u.id

        _login(client, email)
        path = dirty_session_route(user_id)

        with _capture_app_log(app) as records:
            resp = client.get(path)

        assert resp.status_code == 400
        hits = [m for m in records if 'Uncommitted session state at response time' in m]
        assert hits, f'guard did not fire; app.logger saw: {records}'
        assert path in hits[0], 'guard message should name the offending path'

    def test_guard_rolls_back_pending_writes(self, app, client, dirty_session_route):
        """ENFORCING since 2026-09-21 (FEATURES.md #77). Named so a future
        revert back to log-only can't happen by accident — update this test and
        the FEATURES.md entry together if that's ever deliberately reversed.
        """
        with app.app_context():
            u = _make_user(f'guard_rollback_{id(self)}@t.com')
            # Without a Client record a before_request gate redirects to
            # /account-pending and the route under test never runs.
            db.session.add(Client(user_id=u.id, onboarding_completed=True))
            db.session.commit()
            email, user_id = u.email, u.id

        _login(client, email)
        client.get(dirty_session_route(user_id))

        with app.app_context():
            assert db.session.get(User, user_id).lastname != 'LeakedByTest', (
                'the guard did not roll back a pending write at response time'
            )

    def test_guard_is_silent_on_a_normal_request(self, app, client):
        with app.app_context():
            u = _make_user(f'guard_clean_{id(self)}@t.com')
            # Without a Client record a before_request gate redirects to
            # /account-pending and the route under test never runs.
            db.session.add(Client(user_id=u.id, onboarding_completed=True))
            db.session.commit()
            email = u.email

        _login(client, email)
        with _capture_app_log(app) as records:
            resp = client.get('/')

        assert resp.status_code in (200, 302)
        hits = [m for m in records if 'Uncommitted session state' in m]
        assert hits == [], f'guard fired on a clean request: {hits}'

    def test_guard_is_silent_on_the_fixed_both_booking_path(self, app, client):
        """End-to-end cross-check: the guard and the route fix must agree.

        If the fix were incomplete, this path would both persist a booking AND
        trip the guard. Asserting on the guard as well as the data means a future
        regression shows up here rather than only in production logs.
        """
        day = _weekday_soon()
        with app.app_context():
            _make_walker(f'gw_am_{id(self)}@t.com', day.weekday(), 'Morning')
            _make_service()
            admin = _make_user(f'gadm_{id(self)}@t.com', role='walker', is_admin=True)
            owner = _make_user(f'gown_{id(self)}@t.com')
            db.session.add(Client(user_id=owner.id, onboarding_completed=True))
            dog = Dog(name='Buddy', breed='Labrador')
            db.session.add(dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=owner.id, role='primary'))
            db.session.commit()
            admin_email, owner_id, dog_id = admin.email, owner.id, dog.id

        _login(client, admin_email)

        with _capture_app_log(app) as records:
            resp = client.post('/admin/book_for_dog', json={
                'dog_id': dog_id, 'user_id': owner_id,
                'date': day.isoformat(), 'slot': 'Both',
            })

        assert resp.status_code == 400
        guard_hits = [m for m in records if 'Uncommitted session state' in m]
        assert guard_hits == [], f'guard fired on the fixed path: {guard_hits}'
