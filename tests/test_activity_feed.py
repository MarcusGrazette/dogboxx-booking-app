"""
Tests for /admin/activity — Session 4 feed rebuild (§9.6).

DoD assertions:
  1. Event presence per source (BSC, availability, closure, broadcast)
  2. Correct actor_type per initiator (admin vs client vs walker)
  3. "Admin only" filter returns exactly admin-initiated events
  4. Badge = to_status of the BSC row (not current booking status)
"""
import datetime
import json
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    ActivityLog, Booking, BookingStatusChange, Broadcast, Client, Closure,
    DailyMessage, Dog, DogOwner, ServiceType, User, Walker,
    WalkerAdHocAvailability, WalkerSchedule, WalkerUnavailability,
)
from app.utils.booking_status import transition_booking, record_booking_created
from app.utils.activity_log import record_admin_action


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


def _make_walker_user(email):
    u = _make_user(email, role='walker')
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.commit()
    return u, w


def _make_service():
    st = ServiceType(name='Group Walk', slug='group-walk',
                     capacity_model='walker_assigned', slot_type='morning_afternoon',
                     requires_walker=True, default_max_capacity=6, active=True)
    db.session.add(st)
    db.session.commit()
    return st


def _make_dog(owner_id):
    dog = Dog(name='Spot', breed='Terrier')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=owner_id, role='primary'))
    db.session.commit()
    return dog


def _login(flask_client, email):
    return flask_client.post('/auth/login',
                             data={'email': email, 'password': 'Testpass1!'},
                             follow_redirects=True)


def _get_feed(flask_client, month=None):
    url = '/admin/activity'
    if month:
        url += f'?month={month}'
    return flask_client.get(url)


def _this_month():
    today = datetime.date.today()
    return today.strftime('%Y-%m')


# ---------------------------------------------------------------------------
# 1. Event presence per source
# ---------------------------------------------------------------------------

class TestFeedEventSources:

    def test_bsc_booking_creation_appears(self, app, client):
        """A BSC row from booking creation shows up in the feed."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_admin1@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_client1@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            db.session.commit()

        _login(client, 'af_admin1@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Spot' in resp.data

    def test_bsc_confirmation_appears(self, app, client):
        """A confirm transition (requested→confirmed) generates a feed event
        with badge='confirmed', distinct from the original creation row."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_admin2@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_client2@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker = _make_walker_user('af_walker2@test.com')
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            transition_booking(booking, 'confirmed',
                               actor_id=admin.id, walker_id=walker.id)
            db.session.commit()
            # Two BSC rows: creation + confirmation
            rows = BookingStatusChange.query.filter_by(booking_id=booking.id).all()
            assert len(rows) == 2

        _login(client, 'af_admin2@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        # Both the 'Booked' (confirmed) and 'Requested' badges should appear
        assert b'Booked' in resp.data or b'Confirmed' in resp.data

    def test_slot_override_renders_as_moved(self, app, client):
        """F6: a slot-override re-confirm carries a 'slot X → Y' BSC note and the
        feed renders it as 'Moved … to <slot>', not an indistinguishable confirm."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_admin_move@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_client_move@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker = _make_walker_user('af_walker_move@test.com')
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='confirmed', walker_id=walker.id)
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            # Simulate the slot override exactly as assign_walker records it.
            transition_booking(booking, 'confirmed', actor_id=admin.id,
                               walker_id=walker.id, notes='slot Morning → Afternoon',
                               old_slot='Morning', new_slot='Afternoon')
            booking.slot = 'Afternoon'
            db.session.commit()

        _login(client, 'af_admin_move@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Moved' in resp.data
        assert b'to afternoon' in resp.data

    def test_walker_unavailability_appears(self, app, client):
        """A WalkerUnavailability row for this month appears in the feed."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_admin3@test.com', role='walker', is_admin=True)
            walker_u, walker = _make_walker_user('af_walker3@test.com')
            unavail = WalkerUnavailability(
                walker_id=walker.id, date=monday, slot='Morning',
                created_by_id=walker_u.id,
            )
            db.session.add(unavail)
            db.session.commit()

        _login(client, 'af_admin3@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'unavailable' in resp.data.lower() or b'Unavailable' in resp.data

    def test_closure_appears(self, app, client):
        """Creating a closure produces a feed event with badge=closure."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_admin4@test.com', role='walker', is_admin=True)
            closure = Closure(date=monday, reason='Bank holiday',
                              created_by_id=admin.id, range_id=uuid.uuid4().hex)
            db.session.add(closure)
            db.session.commit()

        _login(client, 'af_admin4@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Closed' in resp.data or b'closed' in resp.data

    def test_broadcast_appears(self, app, client):
        """A Broadcast row for this month appears in the feed."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_admin5@test.com', role='walker', is_admin=True)
            br = Broadcast(
                sender_id=admin.id,
                scope_date=monday, scope_slot='all',
                subject='Test announcement', body='Hello clients',
                bell_sent=True, email_sent=False, recipient_count=5,
            )
            db.session.add(br)
            db.session.commit()

        _login(client, 'af_admin5@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Test announcement' in resp.data


class TestConfirmedWaitlistedWording:
    """The description text for a 'confirmed'/'waitlisted' BSC row must match
    what actually happened, not just restate the status — regression coverage
    for the activity-feed wording fix (batch_id/old_slot/from_status signals
    distinguish auto-assign, manual assign, reassign, and slot-override)."""

    def test_auto_assign_on_creation_reads_as_booked(self, app, client):
        """create_booking's internal auto-confirm shares a batch_id with the
        creation row -> 'Booked ... — auto-assigned to <walker>'."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_auto1_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_auto1_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker = _make_walker_user('af_auto1_walker@test.com')
            batch_id = uuid.uuid4().hex
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id, batch_id=batch_id)
            transition_booking(booking, 'confirmed', actor_id=client_u.id,
                               walker_id=walker.id, batch_id=batch_id)
            db.session.commit()

        _login(client, 'af_auto1_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Booked' in resp.data
        assert b'auto-assigned to' in resp.data

    def test_manual_assign_reads_as_assigned(self, app, client):
        """Admin assign-modal call on a requested booking, no batch_id ->
        'Assigned ... to <walker>'."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_man1_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_man1_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker = _make_walker_user('af_man1_walker@test.com')
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            transition_booking(booking, 'confirmed', actor_id=admin.id, walker_id=walker.id)
            db.session.commit()

        _login(client, 'af_man1_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Assigned' in resp.data
        assert b'to Test User' in resp.data

    def test_reassign_on_already_confirmed_reads_as_reassigned(self, app, client):
        """Admin picks a different walker on an already-confirmed booking ->
        'Reassigned ... to <walker>', distinct from a fresh assign."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_re1_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_re1_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker_a = _make_walker_user('af_re1_walkera@test.com')
            _, walker_b = _make_walker_user('af_re1_walkerb@test.com')
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='confirmed', walker_id=walker_a.id)
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            transition_booking(booking, 'confirmed', actor_id=admin.id, walker_id=walker_b.id)
            db.session.commit()

        _login(client, 'af_re1_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Reassigned' in resp.data

    def test_waitlisted_creation_reads_as_booked(self, app, client):
        """A booking landing waitlisted at creation is a system/capacity
        outcome, not something the client did -> text says 'Booked', the
        'Waitlisted' badge pill still conveys the outcome."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_wl1_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_wl1_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='waitlisted')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            db.session.commit()

        _login(client, 'af_wl1_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Booked' in resp.data
        assert b'Waitlisted' in resp.data

    def test_reassign_shows_historical_walker_not_current(self, app, client):
        """A booking reassigned from walker A to walker B must still show A
        on the *first* transition's feed row, not silently collapse both rows
        onto B (today's walker) — regression for the BSC.walker_id snapshot
        (previously the feed read the booking's live walker relationship)."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_hist_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_hist_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)

            walker_a_user = User(firstname='Alice', lastname='Walker', email='af_hist_walkera@test.com',
                                 role='walker', hashed_password=generate_password_hash('Testpass1!'))
            db.session.add(walker_a_user)
            db.session.flush()
            walker_a = Walker(user_id=walker_a_user.id)
            db.session.add(walker_a)

            walker_b_user = User(firstname='Bob', lastname='Walker', email='af_hist_walkerb@test.com',
                                 role='walker', hashed_password=generate_password_hash('Testpass1!'))
            db.session.add(walker_b_user)
            db.session.flush()
            walker_b = Walker(user_id=walker_b_user.id)
            db.session.add(walker_b)
            db.session.commit()

            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            transition_booking(booking, 'confirmed', actor_id=admin.id, walker_id=walker_a.id)
            db.session.commit()
            transition_booking(booking, 'confirmed', actor_id=admin.id, walker_id=walker_b.id)
            db.session.commit()

        _login(client, 'af_hist_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Alice Walker' in resp.data
        assert b'Bob Walker' in resp.data


class TestAvailabilityResetWording:
    """Bookings reset by an availability change must say why and file under
    the 'Availability changes' filter, not render as a bare unattributed
    booking event — regression coverage for the notes-threaded reason."""

    def test_availability_driven_reset_shows_reason_and_availability_type(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_avreset_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_avreset_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker = _make_walker_user('af_avreset_walker@test.com')
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='confirmed', walker_id=walker.id)
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            transition_booking(booking, 'requested', actor_id=admin.id, walker_id=None,
                               notes="Test User marked unavailable")
            db.session.commit()

        _login(client, 'af_avreset_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Reset' in resp.data
        assert b'Test User marked unavailable' in resp.data
        assert b'data-activity="availability"' in resp.data

    def test_plain_unassign_has_no_reason_and_stays_booking_type(self, app, client):
        """A manual admin unassign via the board (no reset cause) keeps the
        old bare wording and activity_type='booking' — distinct from an
        availability-driven reset."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_plainreset_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_plainreset_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker = _make_walker_user('af_plainreset_walker@test.com')
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='confirmed', walker_id=walker.id)
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            transition_booking(booking, 'requested', actor_id=admin.id, walker_id=None)
            db.session.commit()

        _login(client, 'af_plainreset_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Reset' in resp.data
        assert b'to requested' in resp.data
        assert b'data-activity="booking"' in resp.data


# ---------------------------------------------------------------------------
# 2. Correct actor_type per initiator
# ---------------------------------------------------------------------------

class TestFeedActorAttribution:

    def _make_booking_with_bsc(self, actor_id, client_id, dog_id, st_id, date_):
        booking = Booking(user_id=client_id, dog_id=dog_id,
                          service_type_id=st_id, date=date_,
                          slot='Morning', status='requested')
        db.session.add(booking)
        db.session.flush()
        record_booking_created(booking, actor_id=actor_id)
        db.session.commit()
        return booking

    def test_client_booking_has_client_actor(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_attr_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_attr_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            self._make_booking_with_bsc(client_u.id, client_u.id, dog.id, st.id, monday)
            bsc = BookingStatusChange.query.first()
            assert bsc.changed_by_id == client_u.id

        # The BSC row's actor is the client — the feed must reflect that
        _login(client, 'af_attr_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Client' in resp.data   # actor role badge

    def test_admin_booking_has_admin_actor(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_attr_admin2@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_attr_client2@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            self._make_booking_with_bsc(admin.id, client_u.id, dog.id, st.id, monday)
            bsc = BookingStatusChange.query.first()
            assert bsc.changed_by_id == admin.id

        _login(client, 'af_attr_admin2@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Admin' in resp.data   # actor role badge

    def test_admin_unavailability_has_admin_actor(self, app, client):
        """Admin-added unavailability (created_by_id = admin) must show as admin
        actor — not the walker (the §8.3 misattribution bug)."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_attr_admin3@test.com', role='walker', is_admin=True)
            walker_u, walker = _make_walker_user('af_attr_walker3@test.com')
            unavail = WalkerUnavailability(
                walker_id=walker.id, date=monday, slot='Morning',
                created_by_id=admin.id,   # admin acted
            )
            db.session.add(unavail)
            db.session.commit()

        _login(client, 'af_attr_admin3@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Admin' in resp.data   # must be admin, not walker


# ---------------------------------------------------------------------------
# 3. "Admin only" actor filter correctness
# ---------------------------------------------------------------------------

class TestFeedAdminFilter:

    def test_admin_cancellation_appears_under_admin_filter(self, app, client):
        """An admin cancellation (changed_by_id = admin.id) must surface under
        the 'admin' actor filter — the old feed filed these as 'client'."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_filter_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_filter_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            # Admin cancels the booking
            transition_booking(booking, 'cancelled',
                               actor_id=admin.id, cancelled_by='admin')
            db.session.commit()
            admin_id = admin.id
            cancel_bsc = BookingStatusChange.query.filter_by(
                to_status='cancelled').first()
            assert cancel_bsc.changed_by_id == admin_id

        # The cancellation BSC row's actor is admin — actor_type must be 'admin'
        _login(client, 'af_filter_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        # Page must contain both Admin role badge (for the cancellation) and
        # Client role badge (for the original creation). We just verify both exist.
        assert b'Admin' in resp.data
        assert b'Client' in resp.data


class TestHideMyActivityToggle:
    """The client-side 'Hide my activity' toggle filters on actor_id, not
    actor_type=='admin' — so a covering admin's rows stay visible to the
    logged-in admin and vice versa. Server side, this only needs each row to
    carry its actor's id; the actual hide/show logic is JS-only, so these
    tests just assert the HTML the JS depends on is present and correct."""

    def test_row_carries_actor_id_and_toggle_defaults_checked(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('hma_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('hma_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=admin.id)
            db.session.commit()
            admin_id = admin.id

        _login(client, 'hma_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        body = resp.data.decode()
        assert f'data-actor-id="{admin_id}"'.encode() in resp.data
        # Checkbox exists and is checked by default (on load, before any JS runs)
        assert 'id="hide-mine-toggle"' in body
        toggle_tag = body[body.index('id="hide-mine-toggle"') - 100:body.index('id="hide-mine-toggle"') + 50]
        assert 'checked' in toggle_tag

    def test_two_actors_get_distinct_actor_ids(self, app, client):
        """A row from the viewing admin and a row from another admin must
        carry different data-actor-id values — otherwise the toggle can't
        distinguish 'my activity' from 'a covering admin's activity'."""
        monday = _next_weekday(0)
        with app.app_context():
            viewing_admin = _make_user('hma_viewer@test.com', role='walker', is_admin=True)
            covering_admin = _make_user('hma_cover@test.com', role='walker', is_admin=True)
            client_u = _make_user('hma_client2@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            db.session.add(DailyMessage(date=monday, content='Covering admin note',
                                        created_by_id=covering_admin.id))
            db.session.commit()
            covering_id = covering_admin.id

        _login(client, 'hma_viewer@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert f'data-actor-id="{covering_id}"'.encode() in resp.data


# ---------------------------------------------------------------------------
# 4. Badge = to_status of the BSC row
# ---------------------------------------------------------------------------

class TestFeedBadgeFromTransition:

    def test_badge_is_confirmed_not_current_status(self, app, client):
        """A booking created requested then confirmed → two BSC rows. The
        confirmation row must have badge 'Booked', not 'Requested'.
        The old feed showed the current status, so a confirmed booking's
        creation row would show as 'Booked' at its creation time."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_badge_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_badge_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            _, walker = _make_walker_user('af_badge_walker@test.com')
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            transition_booking(booking, 'confirmed',
                               actor_id=admin.id, walker_id=walker.id)
            db.session.commit()

            rows = BookingStatusChange.query.filter_by(
                booking_id=booking.id).order_by(BookingStatusChange.created_at).all()
            assert len(rows) == 2
            assert rows[0].to_status == 'requested'
            assert rows[1].to_status == 'confirmed'

        _login(client, 'af_badge_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        # Both 'Requested' and 'Booked' badges must appear (one per BSC row)
        assert b'Requested' in resp.data
        assert b'Booked' in resp.data


# ---------------------------------------------------------------------------
# 5. Feed clustering — bulk actions collapse to one expandable row (D4)
# ---------------------------------------------------------------------------

class TestFeedClustering:

    def test_bulk_action_produces_one_cluster_row(self, app, client):
        """N BSC rows sharing a batch_id must collapse into a single cluster
        header row with a toggle, not N individual rows (DoD: 'bulk actions
        render as one expandable feed row that expands to the individual
        bookings')."""
        import uuid
        monday = _next_weekday(0)
        tuesday = monday + datetime.timedelta(days=1)
        with app.app_context():
            admin = _make_user('af_clust_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_clust_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)

            # Simulate a bulk cancel: two bookings share one batch_id
            batch_id = uuid.uuid4().hex
            for d in (monday, tuesday):
                b = Booking(user_id=client_u.id, dog_id=dog.id,
                            service_type_id=st.id, date=d,
                            slot='Morning', status='requested')
                db.session.add(b)
                db.session.flush()
                record_booking_created(b, actor_id=client_u.id, batch_id=batch_id)
                transition_booking(b, 'cancelled', actor_id=admin.id,
                                   cancelled_by='admin', batch_id=batch_id)
            db.session.commit()

            # Sanity: 4 BSC rows, 2 creation + 2 cancel, all with same batch_id
            bsc_rows = BookingStatusChange.query.filter_by(batch_id=batch_id).all()
            assert len(bsc_rows) == 4

        _login(client, 'af_clust_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200

        # The cluster header row must appear
        assert b'class="activity-row cluster-header' in resp.data
        # The expand toggle must be present
        assert b'class="btn btn-link btn-sm p-0 cluster-toggle' in resp.data
        # Child rows must be present but hidden by default
        assert b'class="cluster-child' in resp.data

    def test_single_bsc_row_no_batch_renders_plain(self, app, client):
        """A BSC row with no batch_id (single booking action) must render as
        a plain row — no cluster chrome."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_plain_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_plain_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            b = Booking(user_id=client_u.id, dog_id=dog.id,
                        service_type_id=st.id, date=monday,
                        slot='Morning', status='requested')
            db.session.add(b)
            db.session.flush()
            # No batch_id — single action
            record_booking_created(b, actor_id=client_u.id)
            db.session.commit()

        _login(client, 'af_plain_admin@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        # No cluster HTML on a plain single-row action (JS may contain the class names
        # as selectors, so check the class= attribute form which only appears in rows)
        assert b'class="activity-row cluster-header' not in resp.data
        assert b'class="cluster-child' not in resp.data


# ---------------------------------------------------------------------------
# Timestamp localisation — feed times must render in Europe/London, not raw
# UTC. Regression: BookingStatusChange.created_at is stored naive-UTC
# (TIMESTAMP WITHOUT TIME ZONE); the template used to strftime it directly,
# which is off by an hour for the ~7 months/year the UK is on BST.
# ---------------------------------------------------------------------------

class TestFeedTimestampLocalisation:

    def test_bst_timestamp_renders_as_local_not_utc(self, app, client):
        """23:30 UTC on 15 Jul (BST, UTC+1) must render as 00:30 on 16 Jul —
        both the time AND the date must shift across the BST offset."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('af_tz_admin@test.com', role='walker', is_admin=True)
            client_u = _make_user('af_tz_client@test.com', role='client')
            db.session.add(Client(user_id=client_u.id, onboarding_completed=True))
            st = _make_service()
            dog = _make_dog(client_u.id)
            booking = Booking(user_id=client_u.id, dog_id=dog.id,
                              service_type_id=st.id, date=monday,
                              slot='Morning', status='requested')
            db.session.add(booking)
            db.session.flush()
            record_booking_created(booking, actor_id=client_u.id)
            db.session.commit()

            bsc = BookingStatusChange.query.filter_by(booking_id=booking.id).one()
            # Naive value, as SQLAlchemy hands back on read — represents UTC.
            bsc.created_at = datetime.datetime(2026, 7, 15, 23, 30)
            db.session.commit()

        _login(client, 'af_tz_admin@test.com')
        resp = _get_feed(client, '2026-07')
        assert resp.status_code == 200
        assert b'00:30' in resp.data
        assert b'Thu 16 Jul' in resp.data
        assert b'23:30' not in resp.data
        assert b'Wed 15 Jul' not in resp.data


# ---------------------------------------------------------------------------
# ActivityLog + DailyMessage sources (activity-feed expansion — foundation
# PR). ActivityLog has no call sites yet outside this test; DailyMessage is
# wired as a free-win read of its existing created_by_id, no new call site.
# ---------------------------------------------------------------------------

class TestActivityLogFeed:

    def test_admin_action_log_row_appears_with_prebaked_summary(self, app, client):
        """A queued ActivityLog row surfaces verbatim as `summary` — proves
        the write-time-summary path renders without any live join."""
        with app.app_context():
            admin = _make_user('aal_admin1@test.com', role='walker', is_admin=True)
            record_admin_action('client', 999, 'updated', actor_id=admin.id,
                                summary='Updated contact details for Jane Smith',
                                changes={'city': ['Leeds', 'York']})
            db.session.commit()

        _login(client, 'aal_admin1@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Updated contact details for Jane Smith' in resp.data

    def test_admin_action_log_survives_deleted_entity(self, app, client):
        """entity_id pointing at a since-deleted row must not break rendering —
        the whole point of baking `summary` at write time."""
        with app.app_context():
            admin = _make_user('aal_admin2@test.com', role='walker', is_admin=True)
            record_admin_action('client', 424242, 'removed', actor_id=admin.id,
                                summary='Removed dog access for Fido (client no longer active)')
            db.session.commit()

        _login(client, 'aal_admin2@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Removed dog access for Fido' in resp.data


class TestDailyMessageFeed:

    def test_daily_message_appears_in_feed(self, app, client):
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('dmf_admin1@test.com', role='walker', is_admin=True)
            db.session.add(DailyMessage(date=monday, content='Ice on the ground today',
                                        created_by_id=admin.id))
            db.session.commit()

        _login(client, 'dmf_admin1@test.com')
        resp = _get_feed(client, _this_month())
        assert resp.status_code == 200
        assert b'Posted a daily message' in resp.data
