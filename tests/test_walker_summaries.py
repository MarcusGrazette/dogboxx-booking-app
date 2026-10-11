"""Daily walker summaries + the 7-day instant-notification window for walkers.

- walker_notify_now(): instant walker notifications only for walks from
  today to WALKER_NOTICE_DAYS ahead; further out is left to the summaries.
- due_summary(): 08:00 London → today's 'morning', 15:00 → next working
  day's 'preview' (Friday → Monday), in both GMT and BST; nothing at weekends.
- build_summary(): slot order, walks before drop-ins, pickup order, the day's
  admin announcement as plain text.
- send_walker_summaries(): confirmed bookings of eligible walkers only, one
  per walker, nothing for walkers with no walks, idempotent on re-run.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import db as _db
from app.models import (
    Booking, DailyMessage, Dog, DogOwner, Notification, ServiceType, Walker,
    WalkerSummarySent,
)
from app.utils.dates import local_today
from app.utils.notifications import (
    WALKER_NOTICE_DAYS, NotificationBatch, walker_notify_now,
)
from app.utils import walker_summaries
from app.utils.walker_summaries import (
    build_summary, due_summary, next_working_day, send_walker_summaries,
)
from tests.conftest import login, make_user

DAY = date(2099, 6, 2)   # a Tuesday


def _drop_in_service():
    st = ServiceType(
        name='Drop In', slug='drop-in', capacity_model='walker_assigned',
        slot_type='morning_afternoon', requires_walker=True,
        default_max_capacity=6, active=True,
    )
    _db.session.add(st)
    _db.session.commit()
    return st


def _dog(owner, name):
    d = Dog(name=name, breed='Mixed')
    _db.session.add(d)
    _db.session.flush()
    _db.session.add(DogOwner(dog_id=d.id, user_id=owner.id, role='primary'))
    _db.session.commit()
    return d


def _booking(owner, dog, service, walker, *, day=DAY, slot='Morning',
             status='confirmed', pickup_order=None):
    b = Booking(user_id=owner.id, dog_id=dog.id, service_type_id=service.id,
                date=day, slot=slot, status=status, walker_id=walker.id,
                pickup_order=pickup_order)
    _db.session.add(b)
    _db.session.commit()
    return b


def _summaries_for(user_id):
    return Notification.query.filter_by(
        recipient_id=user_id, notification_type='walker_summary').all()


@pytest.fixture
def walker(walker_user):
    return Walker.query.filter_by(user_id=walker_user.id).one()


# ── 7-day window ────────────────────────────────────────────────────────────

class TestWalkerNotifyNow:
    def test_window_edges(self):
        today = date(2026, 10, 12)
        assert walker_notify_now(today, today)
        assert walker_notify_now(today + timedelta(days=WALKER_NOTICE_DAYS), today)
        assert not walker_notify_now(today + timedelta(days=WALKER_NOTICE_DAYS + 1), today)
        assert not walker_notify_now(today - timedelta(days=1), today)

    def test_batch_add_for_walker_drops_far_future(self, app, walker_user):
        today = local_today()
        batch = NotificationBatch(actor_id=None)
        batch.add_for_walker(walker_user.id, 'walker_assigned', dog_name='Rex',
                             slot='Morning', date=today + timedelta(days=30))
        assert batch.flush() == []
        batch.add_for_walker(walker_user.id, 'walker_assigned', dog_name='Rex',
                             slot='Morning', date=today + timedelta(days=2))
        assert len(batch.flush()) == 1


class TestCancelRespectsWindow:
    """cancel_booking's walker notification — one of the direct
    create_notification walker sends, outside NotificationBatch."""

    def _cancel_titles(self, client, booking, walker_user):
        resp = client.post('/cancel_booking', data={'booking_id': booking.id})
        assert resp.get_json()['success'] is True
        return [n.title for n in Notification.query.filter_by(
            recipient_id=walker_user.id, notification_type='booking_cancelled')]

    def test_far_future_cancel_not_sent_to_walker(
            self, app, client, client_user, dog, service_type, walker_user, walker):
        b = _booking(client_user, dog, service_type, walker,
                     day=local_today() + timedelta(days=30))
        login(client, client_user.email)
        assert self._cancel_titles(client, b, walker_user) == []

    def test_near_future_cancel_sent_to_walker(
            self, app, client, client_user, dog, service_type, walker_user, walker):
        b = _booking(client_user, dog, service_type, walker,
                     day=local_today() + timedelta(days=3))
        login(client, client_user.email)
        assert len(self._cancel_titles(client, b, walker_user)) == 1


# ── When summaries are due ──────────────────────────────────────────────────

def _utc(y, m, d, h, minute=0):
    return datetime(y, m, d, h, minute, tzinfo=timezone.utc)


class TestDueSummary:
    def test_morning_gmt_and_bst(self):
        # Mon 2 Nov 2026 is GMT: 08:00 London = 08:00 UTC
        assert due_summary(_utc(2026, 11, 2, 8)) == ('morning', date(2026, 11, 2))
        assert due_summary(_utc(2026, 11, 2, 7)) is None
        # Mon 12 Oct 2026 is BST: 08:00 London = 07:00 UTC
        assert due_summary(_utc(2026, 10, 12, 7)) == ('morning', date(2026, 10, 12))
        assert due_summary(_utc(2026, 10, 12, 8)) is None

    def test_preview_targets_next_working_day(self):
        # Tue 13 Oct 2026, BST: 15:00 London = 14:00 UTC
        assert due_summary(_utc(2026, 10, 13, 14)) == ('preview', date(2026, 10, 14))
        # Fri 6 Nov 2026, GMT: Friday's preview covers Monday
        assert due_summary(_utc(2026, 11, 6, 15)) == ('preview', date(2026, 11, 9))

    def test_nothing_at_weekends(self):
        assert due_summary(_utc(2026, 11, 7, 8)) is None    # Saturday
        assert due_summary(_utc(2026, 11, 8, 15)) is None   # Sunday

    def test_next_working_day(self):
        assert next_working_day(date(2026, 11, 5)) == date(2026, 11, 6)   # Thu → Fri
        assert next_working_day(date(2026, 11, 6)) == date(2026, 11, 9)   # Fri → Mon


# ── Summary text ────────────────────────────────────────────────────────────

class TestBuildSummary:
    def test_slots_drop_ins_and_pickup_order(
            self, app, client_user, service_type, walker):
        drop_in = _drop_in_service()
        rex, bella, milo, biscuit = (_dog(client_user, n)
                                     for n in ('Rex', 'Bella', 'Milo', 'Biscuit'))
        rows = [
            _booking(client_user, rex, service_type, walker, slot='Afternoon', pickup_order=1),
            _booking(client_user, milo, service_type, walker, slot='Morning', pickup_order=2),
            _booking(client_user, bella, service_type, walker, slot='Morning', pickup_order=1),
            _booking(client_user, biscuit, drop_in, walker, slot='Morning'),
        ]
        title, body, link = build_summary('morning', DAY, rows)
        assert title == 'Your walks today (Tue 2 Jun)'
        assert body == ('Morning: Bella, Milo · Morning drop-ins: Biscuit · '
                        'Afternoon: Rex')
        assert link == '/walker/pickups/2099-06-02'

    def test_preview_titles(self, app, client_user, dog, service_type, walker):
        rows = [_booking(client_user, dog, service_type, walker)]
        assert build_summary('preview', DAY, rows)[0] == 'Your walks tomorrow (Tue 2 Jun)'
        monday = date(2099, 6, 1)
        assert build_summary('preview', monday, rows)[0] == 'Your walks on Monday (Mon 1 Jun)'

    def test_daily_message_appended_as_plain_text(
            self, app, client_user, dog, service_type, walker):
        rows = [_booking(client_user, dog, service_type, walker)]
        _, body, _ = build_summary('morning', DAY, rows,
                                   '<p>Park gate <strong>closed</strong> &amp; muddy</p>')
        assert body == 'Morning: Buddy · Note: Park gate closed & muddy'

    def test_long_daily_message_truncated(
            self, app, client_user, dog, service_type, walker):
        rows = [_booking(client_user, dog, service_type, walker)]
        _, body, _ = build_summary('morning', DAY, rows, '<p>' + 'x' * 500 + '</p>')
        note = body.split('Note: ', 1)[1]
        assert len(note) == 200 and note.endswith('…')


# ── Sending ─────────────────────────────────────────────────────────────────

class TestSendWalkerSummaries:
    def test_sends_one_per_walker_with_confirmed_walks(
            self, app, client_user, dog, service_type, walker_user, walker, admin_user):
        idle = make_user(firstname='Idle', role='walker')
        _db.session.add(Walker(user_id=idle.id))
        _db.session.commit()
        other = _dog(client_user, 'Rex')
        _booking(client_user, dog, service_type, walker, slot='Morning')
        _booking(client_user, other, service_type, walker, slot='Afternoon')

        assert send_walker_summaries('morning', DAY) == 1
        notifs = _summaries_for(walker_user.id)
        assert [n.body for n in notifs] == ['Morning: Buddy · Afternoon: Rex']
        assert _summaries_for(idle.id) == []
        # Admins get no summary unless they walk that day.
        assert _summaries_for(admin_user.id) == []

    def test_only_confirmed_bookings_count(
            self, app, client_user, dog, service_type, walker_user, walker):
        other = _dog(client_user, 'Rex')
        _booking(client_user, dog, service_type, walker, status='cancelled')
        _booking(client_user, other, service_type, walker, status='requested',
                 slot='Afternoon')
        assert send_walker_summaries('morning', DAY) == 0
        assert _summaries_for(walker_user.id) == []

    def test_demoted_walker_skipped(
            self, app, client_user, dog, service_type, walker_user, walker):
        _booking(client_user, dog, service_type, walker)
        walker_user.role = 'client'
        _db.session.commit()
        assert send_walker_summaries('morning', DAY) == 0

    def test_rerun_is_a_noop(self, app, client_user, dog, service_type, walker_user, walker):
        _booking(client_user, dog, service_type, walker)
        assert send_walker_summaries('morning', DAY) == 1
        assert send_walker_summaries('morning', DAY) == 0
        assert len(_summaries_for(walker_user.id)) == 1
        # The preview for the same day is a separate summary.
        assert send_walker_summaries('preview', DAY) == 1
        assert WalkerSummarySent.query.filter_by(walker_id=walker.id).count() == 2

    def test_includes_daily_message(
            self, app, client_user, dog, service_type, walker_user, walker):
        _booking(client_user, dog, service_type, walker)
        _db.session.add(DailyMessage(date=DAY, content='<p>Bring towels</p>'))
        _db.session.commit()
        send_walker_summaries('morning', DAY)
        assert _summaries_for(walker_user.id)[0].body == 'Morning: Buddy · Note: Bring towels'

    def test_failure_for_one_walker_does_not_block_others(
            self, app, client_user, dog, service_type, walker_user, walker):
        second_user = make_user(firstname='Second', role='walker')
        second = Walker(user_id=second_user.id)
        _db.session.add(second)
        _db.session.commit()
        _booking(client_user, dog, service_type, walker)
        _booking(client_user, _dog(client_user, 'Rex'), service_type, second)

        original = walker_summaries.create_notification
        calls = []

        def flaky(**kw):
            calls.append(kw['recipient_id'])
            if len(calls) == 1:
                raise RuntimeError('boom')
            return original(**kw)

        with patch('app.utils.walker_summaries.create_notification', side_effect=flaky):
            assert send_walker_summaries('morning', DAY) == 1
        # The failed walker has no sent-marker, so a re-run picks them up.
        assert send_walker_summaries('morning', DAY) == 1
        assert WalkerSummarySent.query.count() == 2
