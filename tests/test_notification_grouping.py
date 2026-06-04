"""
Tests for notification grouping + text unification (NOTIFICATIONS.md §9.3/§9.4,
Session 2).

Two layers:
  - summarise()        — pure text source (no DB). The bulk of the wording spec.
  - NotificationBatch  — buckets intents per (recipient, kind) and emits one
                         grouped notification each (needs app + DB).
"""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import Notification, User
from app.utils.notifications import (
    summarise,
    NotificationBatch,
    _KIND_NTYPE,
)


# ── summarise(): single-item, reflexive (owner) ──────────────────────────────

def test_single_confirmed_reflexive():
    title, body, ntype, link = summarise('booking_confirmed', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1),
         'walker_name': 'Alice'},
    ])
    assert title == "Daisy's morning walk on Mon 1 Jun confirmed"
    assert body == "Booked with Alice."
    assert ntype == 'booking_confirmed'
    assert link == '/notifications'


def test_single_confirmed_no_walker():
    title, body, _, _ = summarise('booking_confirmed', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1)},
    ])
    assert body == "Walker assigned."


def test_single_requested_reflexive():
    title, body, ntype, _ = summarise('booking_requested', [
        {'dog_name': 'Rex', 'slot': 'Afternoon', 'date': date(2026, 6, 2)},
    ])
    assert title == "Rex's afternoon walk on Tue 2 Jun requested"
    assert body == "We'll confirm shortly."
    assert ntype == 'booking_requested'


def test_single_waitlisted_reflexive():
    title, body, ntype, _ = summarise('booking_waitlisted', [
        {'dog_name': 'Rex', 'slot': 'Morning', 'date': date(2026, 6, 2)},
    ])
    assert title == "Rex's morning walk on Tue 2 Jun is on the waitlist"
    assert "spot opens up" in body
    # waitlisted is styled as a 'requested' notification (§2)
    assert ntype == 'booking_requested'


def test_single_cancelled_with_reason():
    title, body, ntype, _ = summarise('booking_cancelled', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1),
         'reason': 'DogBoxx is closed — Bank holiday.'},
    ])
    assert title == "Daisy's morning walk on Mon 1 Jun cancelled"
    assert body == 'DogBoxx is closed — Bank holiday.'
    assert ntype == 'booking_cancelled'


def test_single_dropin_label():
    title, _, _, _ = summarise('booking_confirmed', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1),
         'svc_label': 'drop-in', 'walker_name': 'Alice'},
    ])
    assert "drop-in" in title and "walk" not in title


# ── summarise(): single-item, actor-prefixed (admin-on-behalf / fan-out) ─────

def test_single_confirmed_actor_prefixed():
    title, body, _, _ = summarise('booking_confirmed', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1),
         'walker_name': 'Alice'},
    ], actor_first='Lydia')
    assert title == "Lydia booked Daisy's morning walk on Mon 1 Jun"
    assert body == "Booked with Alice."


def test_single_requested_actor_prefixed():
    title, body, _, _ = summarise('booking_requested', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1)},
    ], actor_first='John')
    assert title == "John requested Daisy's morning walk on Mon 1 Jun"
    assert body is None


def test_single_cancelled_actor_prefixed():
    title, _, _, _ = summarise('booking_cancelled', [
        {'dog_name': 'Daisy', 'slot': 'Afternoon', 'date': date(2026, 6, 1)},
    ], actor_first='Lydia')
    assert title == "Lydia cancelled Daisy's afternoon walk on Mon 1 Jun"


# ── summarise(): grouped (N rows) ────────────────────────────────────────────

def test_grouped_confirmed_reflexive_one_dog():
    payloads = [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, d)}
        for d in (1, 2, 3, 4, 5)
    ]
    title, body, ntype, _ = summarise('booking_confirmed', payloads)
    assert title == "Daisy's 5 walks confirmed (Mon 1 – Fri 5 Jun)"
    assert ntype == 'booking_confirmed'


def test_grouped_confirmed_actor_prefixed_one_dog():
    payloads = [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, d)}
        for d in (1, 2, 3, 4, 5)
    ]
    title, _, _, _ = summarise('booking_confirmed', payloads, actor_first='Lydia')
    assert title == "Lydia booked Daisy's 5 walks (Mon 1 – Fri 5 Jun)"


def test_grouped_cancelled_multi_dog():
    payloads = [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1)},
        {'dog_name': 'Rex',   'slot': 'Morning', 'date': date(2026, 6, 3)},
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 5)},
    ]
    title, body, _, _ = summarise('booking_cancelled', payloads)
    # No single-dog prefix when more than one dog is involved.
    assert title == "3 walks cancelled (Mon 1 – Fri 5 Jun)"
    assert 'Daisy' in body and 'Rex' in body


def test_grouped_dropin_pluralisation():
    payloads = [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1),
         'svc_label': 'drop-in'},
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 2),
         'svc_label': 'drop-in'},
    ]
    title, _, _, _ = summarise('booking_cancelled', payloads)
    assert "2 drop-ins cancelled" in title


def test_range_crosses_month():
    payloads = [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 5, 30)},
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 2)},
    ]
    title, _, _, _ = summarise('booking_confirmed', payloads)
    assert "(Sat 30 May – Tue 2 Jun)" in title


# ── summarise(): walker_assigned ─────────────────────────────────────────────

def test_walker_assigned_single():
    title, body, ntype, link = summarise('walker_assigned', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1)},
    ])
    assert title == "You have been assigned a walk on 1 Jun 2026"
    assert body == "Daisy — Morning"
    assert ntype == 'walker_assigned'
    assert link == '/walker/pickups?date=2026-06-01'


def test_walker_assigned_grouped():
    payloads = [
        {'dog_name': 'Rex',   'slot': 'Morning', 'date': date(2026, 6, 1)},
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 5)},
    ]
    title, body, _, link = summarise('walker_assigned', payloads)
    assert title == "You have been assigned 2 walks (Mon 1 – Fri 5 Jun)"
    assert body == "Daisy, Rex"          # sorted dog names
    assert link == '/walker/schedule'


# ── summarise(): booking_reset ───────────────────────────────────────────────

def test_reset_single_dropin_label():
    """Single reset respects svc_label (already worked pre-F5)."""
    title, _, ntype, _ = summarise('booking_reset', [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1),
         'svc_label': 'drop-in'},
    ])
    assert title == "Daisy's Mon 1 Jun drop-in needs a new walker"
    assert ntype == 'system'


def test_reset_grouped_dropin_label():
    """F5 regression: grouped reset must use svc_label, not hard-coded 'walks'."""
    payloads = [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1),
         'svc_label': 'drop-in'},
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 2),
         'svc_label': 'drop-in'},
    ]
    title, _, _, _ = summarise('booking_reset', payloads)
    assert title == "2 of your drop-ins are being reassigned"


def test_reset_grouped_walk_label():
    """Grouped reset of walks still says 'walks'."""
    payloads = [
        {'dog_name': 'Daisy', 'slot': 'Morning', 'date': date(2026, 6, 1)},
        {'dog_name': 'Rex',   'slot': 'Morning', 'date': date(2026, 6, 2)},
    ]
    title, _, _, _ = summarise('booking_reset', payloads)
    assert title == "2 of your walks are being reassigned"


# ── summarise(): contract guards ─────────────────────────────────────────────

def test_empty_payloads_raises():
    with pytest.raises(ValueError):
        summarise('booking_confirmed', [])


def test_kind_ntype_mapping_complete():
    # Every kind summarise() accepts must have a styling key.
    for kind in ('booking_confirmed', 'booking_requested', 'booking_waitlisted',
                 'booking_cancelled', 'walker_assigned'):
        assert kind in _KIND_NTYPE


# ── NotificationBatch ────────────────────────────────────────────────────────

def _make_user(email):
    u = User(email=email, firstname='Test', lastname='User', role='client',
             hashed_password=generate_password_hash('x'))
    db.session.add(u)
    db.session.commit()
    return u


def test_batch_groups_same_recipient_kind(app, db):
    """5 confirmed walks for one client → exactly ONE notification (the DoD)."""
    client = _make_user('batch-group@example.com')
    batch = NotificationBatch(actor_id=client.id)
    for d in (1, 2, 3, 4, 5):
        batch.add(client.id, 'booking_confirmed',
                  dog_name='Daisy', slot='Morning', date=date(2026, 6, d))
    rows = batch.flush()
    db.session.commit()

    assert len(rows) == 1
    notif = Notification.query.filter_by(recipient_id=client.id).one()
    assert notif.title == "Daisy's 5 walks confirmed (Mon 1 – Fri 5 Jun)"
    assert notif.notification_type == 'booking_confirmed'


def test_batch_admin_on_behalf_actor_prefix(app, db):
    """Admin booking 5 recurring walks → ONE client notification with actor prefix."""
    admin  = _make_user('batch-admin@example.com')
    client = _make_user('batch-client@example.com')
    batch = NotificationBatch(actor_id=admin.id)
    for d in (1, 2, 3, 4, 5):
        batch.add(client.id, 'booking_confirmed', actor_first='Lydia',
                  dog_name='Daisy', slot='Morning', date=date(2026, 6, d))
    batch.flush()
    db.session.commit()

    notif = Notification.query.filter_by(recipient_id=client.id).one()
    assert notif.title == "Lydia booked Daisy's 5 walks (Mon 1 – Fri 5 Jun)"
    assert notif.sender_id == admin.id


def test_batch_separates_recipients_and_kinds(app, db):
    a = _make_user('batch-a@example.com')
    b = _make_user('batch-b@example.com')
    batch = NotificationBatch(actor_id=a.id)
    batch.add(a.id, 'booking_confirmed', dog_name='Daisy', slot='Morning', date=date(2026, 6, 1))
    batch.add(b.id, 'booking_confirmed', dog_name='Daisy', slot='Morning', date=date(2026, 6, 1))
    batch.add(a.id, 'booking_cancelled', dog_name='Daisy', slot='Morning', date=date(2026, 6, 2))
    batch.flush()
    db.session.commit()

    assert Notification.query.filter_by(recipient_id=a.id).count() == 2  # two kinds
    assert Notification.query.filter_by(recipient_id=b.id).count() == 1


def test_batch_link_override(app, db):
    admin  = _make_user('batch-link-admin@example.com')
    client = _make_user('batch-link-client@example.com')
    batch = NotificationBatch(actor_id=admin.id)
    batch.add(admin.id, 'booking_confirmed', actor_first='John',
              link=f'/admin/clients/{client.id}',
              dog_name='Daisy', slot='Morning', date=date(2026, 6, 1))
    batch.flush()
    db.session.commit()

    notif = Notification.query.filter_by(recipient_id=admin.id).one()
    assert notif.link == f'/admin/clients/{client.id}'


def test_flush_clears_batch(app, db):
    u = _make_user('batch-clear@example.com')
    batch = NotificationBatch(actor_id=u.id)
    batch.add(u.id, 'booking_confirmed', dog_name='Daisy', slot='Morning', date=date(2026, 6, 1))
    batch.flush()
    db.session.commit()
    # Second flush is a no-op — no duplicate row.
    assert batch.flush() == []
    db.session.commit()
    assert Notification.query.filter_by(recipient_id=u.id).count() == 1


# ── Session 5: cap-pruning test (D1) ─────────────────────────────────────────

def test_db_cap_prunes_oldest(app, db):
    """Creating NOTIF_DB_CAP+1 notifications for one user must leave exactly
    NOTIF_DB_CAP rows, with the oldest one pruned (D1: cap bumped to 100)."""
    from app.utils.notifications import create_notification, NOTIF_DB_CAP
    u = _make_user('cap-test@example.com')

    for i in range(NOTIF_DB_CAP + 1):
        create_notification(
            recipient_id=u.id,
            notification_type='system',
            title=f'Notification {i}',
            sender_id=u.id,
        )
    db.session.commit()

    stored = Notification.query.filter_by(recipient_id=u.id).count()
    assert stored == NOTIF_DB_CAP, (
        f"Expected {NOTIF_DB_CAP} notifications after pruning, got {stored}"
    )
