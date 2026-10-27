"""
Client & dog deletion (FEATURES #100) — anonymise in place, dog-first.

The rows, ids and DogOwner links stay so billing is unchanged; personal data
and free text are wiped. Super-admin only, blocked by upcoming bookings, and
every check the modal shows is repeated by the server.
"""
import datetime
import os
import types

import pytest
from flask import g
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    ActivityLog, Booking, BookingStatusChange, Client, Dog, DogOwner,
    Notification, PricingConfig, PushSubscription, ServiceType, User, Walker,
)
from app.utils.booking_access import get_accessible_dog_ids, user_can_access_booking
from app.utils import uploads as uploads_mod
from app.utils.dates import local_today
from app.utils.invoicing import invoice_for_client

from tests.conftest import login, make_user


# ── helpers ──────────────────────────────────────────────────────────────────

def _super_admin():
    u = make_user(firstname='Lydia', lastname='Owner', email='owner@test.dogboxx.org',
                  role='walker', is_admin=True)
    u.is_super_admin = True
    db.session.add(Walker(user_id=u.id))
    db.session.commit()
    return u


def _client(first, last, email=None):
    u = make_user(firstname=first, lastname=last, email=email)
    db.session.add(Client(user_id=u.id, onboarding_completed=True,
                          street_address='1 High St', postal_code='N1 1AA',
                          city='London', maps_url='https://maps.example/x'))
    u.phone = '07700 900000'
    db.session.commit()
    return u


def _dog(name, *owners, **fields):
    dog = Dog(name=name, breed='Labrador', allergies='Chicken', other_info='Shy',
              pickup_instructions='<p>Key under mat</p>', whatsapp_group_url='https://wa.example/x',
              hold_key=True, gender='male', date_of_birth=datetime.date(2020, 1, 1), **fields)
    db.session.add(dog)
    db.session.flush()
    for i, u in enumerate(owners):
        db.session.add(DogOwner(dog_id=dog.id, user_id=u.id, role='primary' if i == 0 else 'secondary'))
    db.session.commit()
    return dog


def _walk_service():
    st = ServiceType.query.filter_by(slug='group-walk').first()
    if st:
        return st
    st = ServiceType(name='Group Walk', slug='group-walk', capacity_model='walker_assigned',
                     slot_type='morning_afternoon', requires_walker=True,
                     default_max_capacity=6, active=True)
    db.session.add(st)
    db.session.commit()
    return st


def _booking(user, dog, date, slot='Morning', status='confirmed', **fields):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=_walk_service().id,
                date=date, slot=slot, status=status, **fields)
    db.session.add(b)
    db.session.commit()
    return b


def _preview(c, dog):
    return c.get(f'/admin/dogs/{dog.id}/delete-preview', headers={'Accept': 'application/json'})


def _delete_dog(c, dog, owner_ids=(), typed_name=None):
    return c.post(f'/admin/dogs/{dog.id}/delete', json={
        'owner_ids': list(owner_ids),
        'typed_name': dog.name if typed_name is None else typed_name,
    })


def _as(flask_client):
    """Switch which test client is "current". Test requests share the
    fixture's app context, so Flask-Login's per-context cache (g._login_user)
    would otherwise keep answering with the first user it loaded."""
    g.pop('_login_user', None)
    return flask_client


def _fresh(model, id_):
    db.session.expire_all()
    return db.session.get(model, id_)


@pytest.fixture
def owner(client):
    u = _super_admin()
    login(client, u.email)
    return u


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(uploads_mod, 'gevent',
                        types.SimpleNamespace(spawn=lambda fn, *args: calls.append((fn, args))))
    return calls


def _place(app, subfolder, filename):
    folder = os.path.join(os.path.dirname(app.config['UPLOAD_FOLDER']), subfolder)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, 'wb') as f:
        f.write(b'photo')
    return path


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:

    def test_non_super_admin_gets_403(self, logged_in_admin):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        dogless = _client('Dan', 'Dogless')
        assert _preview(logged_in_admin, rex).status_code == 403
        assert _delete_dog(logged_in_admin, rex).status_code == 403
        r = logged_in_admin.post(f'/admin/clients/{dogless.id}/delete', json={'typed_name': 'Dan Dogless'})
        assert r.status_code == 403
        assert _fresh(Dog, rex.id).deleted_at is None
        assert _fresh(User, dogless.id).deleted_at is None

    @pytest.mark.parametrize('status', ['requested', 'confirmed', 'waitlisted', 'modified'])
    def test_upcoming_booking_blocks(self, client, owner, status):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        _booking(jane, rex, local_today(), status=status)
        data = _preview(client, rex).get_json()
        assert len(data['future_bookings']) == 1
        r = _delete_dog(client, rex)
        assert r.status_code == 409
        assert _fresh(Dog, rex.id).deleted_at is None

    def test_cancelled_and_past_bookings_dont_block(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        _booking(jane, rex, local_today() + datetime.timedelta(days=3), status='cancelled')
        _booking(jane, rex, local_today() - datetime.timedelta(days=3))
        assert _preview(client, rex).get_json()['future_bookings'] == []
        assert _delete_dog(client, rex).status_code == 200

    def test_typed_name_must_match(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        assert _delete_dog(client, rex, typed_name='Max').status_code == 400
        assert _fresh(Dog, rex.id).deleted_at is None
        # Case and surrounding spaces don't matter.
        assert _delete_dog(client, rex, typed_name='  rex ').status_code == 200

    def test_ineligible_owners_rejected_by_server(self, client, owner):
        jane = _client('Jane', 'Smith')
        walker = make_user(firstname='Wally', lastname='Walker', role='walker')
        db.session.add(Client(user_id=walker.id, onboarding_completed=True))
        db.session.commit()
        rex = _dog('Rex', jane, walker, owner)
        _dog('Bella', jane)

        owners = {o['id']: o for o in _preview(client, rex).get_json()['owners']}
        assert owners[jane.id]['can_delete'] is False
        assert 'also owns Bella' in owners[jane.id]['reason']
        assert 'walker' in owners[walker.id]['reason']
        assert owners[owner.id]['can_delete'] is False

        for uid in (jane.id, walker.id, owner.id):
            r = _delete_dog(client, rex, owner_ids=[uid])
            assert r.status_code == 400, uid
        assert _fresh(Dog, rex.id).deleted_at is None
        assert _fresh(User, jane.id).deleted_at is None

    def test_owner_id_must_belong_to_the_dog(self, client, owner):
        jane = _client('Jane', 'Smith')
        stranger = _client('Sam', 'Stranger')
        rex = _dog('Rex', jane)
        assert _delete_dog(client, rex, owner_ids=[stranger.id]).status_code == 400
        assert _fresh(User, stranger.id).deleted_at is None

    def test_already_deleted_dog_is_404(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        assert _delete_dog(client, rex).status_code == 200
        rex = _fresh(Dog, rex.id)
        assert _delete_dog(client, rex).status_code == 404
        assert _preview(client, rex).status_code == 404


# ── what gets wiped ──────────────────────────────────────────────────────────

class TestAnonymise:

    def test_dog_and_ticked_owner_are_wiped(self, app, client, owner, spawned):
        jane = _client('Jane', 'Smith')
        jane.profile_pic = 'jane.jpg'
        rex = _dog('Rex', jane, pic='rex.jpg', pickup_notes_photo='rexkey.jpg')
        paths = [_place(app, 'profiles', 'jane.jpg'), _place(app, 'dogs', 'rex.jpg'),
                 _place(app, 'pickup_notes', 'rexkey.jpg')]
        past = _booking(jane, rex, local_today() - datetime.timedelta(days=7),
                        client_notes='Ring twice', admin_notes='VIP')
        db.session.add(BookingStatusChange(booking_id=past.id, from_status='requested',
                                           to_status='confirmed', changed_by_id=jane.id,
                                           notes='Jane says hi', old_slot='Morning'))
        db.session.add(ActivityLog(entity_type='dog', entity_id=rex.id, action='updated',
                                   actor_id=jane.id, summary='Jane Smith updated Rex',
                                   changes={'allergies': ['a', 'b']}))
        db.session.add(ActivityLog(entity_type='client', entity_id=jane.id, action='updated',
                                   actor_id=owner.id, summary='Updated Jane Smith',
                                   changes={'phone': ['1', '2']}))
        db.session.add(ActivityLog(entity_type='booking_note', entity_id=past.id, action='updated',
                                   actor_id=owner.id, summary="Edited the note on Rex's walk"))
        db.session.add(Notification(recipient_id=jane.id, notification_type='system', title='Hi Jane', body='x'))
        db.session.add(PushSubscription(user_id=jane.id, endpoint='https://push.example/1',
                                        p256dh='k', auth='a'))
        db.session.commit()

        r = _delete_dog(client, rex, owner_ids=[jane.id])
        assert r.status_code == 200, r.get_json()

        rex = _fresh(Dog, rex.id)
        assert rex.name == f'[Deleted dog #{rex.id}]'
        assert rex.deleted_at is not None
        for f in ('breed', 'date_of_birth', 'gender', 'allergies', 'other_info',
                  'whatsapp_group_url', 'pickup_instructions', 'pic', 'pickup_notes_photo'):
            assert getattr(rex, f) is None, f
        assert rex.hold_key is False

        jane = _fresh(User, jane.id)
        assert jane.full_name == f'[Deleted client #{jane.id}]'
        assert jane.email == f'deleted-{jane.id}@deleted.invalid'
        assert jane.phone is None and jane.profile_pic is None
        assert jane.active is False and jane.email_marketing is False
        assert jane.deleted_at is not None
        assert jane.initials == '?'
        c = jane.client
        assert (c.street_address, c.city, c.postal_code, c.maps_url) == (None, None, None, None)

        b = _fresh(Booking, past.id)
        assert b.client_notes is None and b.admin_notes is None
        bsc = BookingStatusChange.query.filter_by(booking_id=past.id).one()
        assert bsc.notes is None and bsc.old_slot == 'Morning'

        for row in ActivityLog.query.filter(ActivityLog.entity_type.in_(['dog', 'client', 'booking_note'])):
            assert 'Jane' not in row.summary and 'Rex' not in row.summary, row.summary
        assert all(row.changes is None for row in ActivityLog.query.all())
        audit = ActivityLog.query.filter_by(entity_type='dog', action='removed').one()
        assert audit.summary == f'Deleted [Deleted dog #{rex.id}] and [Deleted client #{jane.id}]'

        assert Notification.query.filter_by(recipient_id=jane.id).count() == 0
        assert PushSubscription.query.filter_by(user_id=jane.id).count() == 0
        # The links stay — invoices find the dog through its primary owner.
        assert DogOwner.query.filter_by(dog_id=rex.id, user_id=jane.id).count() == 1

        for p in paths:
            assert not os.path.exists(p), p
        r2 = sorted(args[0] for fn, args in spawned if fn is uploads_mod._delete_from_r2)
        assert r2 == ['dogs/rex.jpg', 'pickup_notes/rexkey.jpg', 'profiles/jane.jpg']

    def test_deleted_client_is_locked_out(self, app, client, owner):
        jane = _client('Jane', 'Smith', email='jane@example.com')
        rex = _dog('Rex', jane)
        jane_client = app.test_client()
        login(_as(jane_client), 'jane@example.com')
        assert jane_client.get('/profile').status_code == 200

        assert _delete_dog(_as(client), rex, owner_ids=[jane.id]).status_code == 200

        # Existing session is dead, and the old password no longer works.
        r = _as(jane_client).get('/profile')
        assert r.status_code == 302 and '/auth/login' in r.headers['Location']
        login(_as(jane_client), 'jane@example.com')
        assert _as(jane_client).get('/profile').status_code == 302

        # No way back in from the admin side.
        _as(client)
        assert client.post(f'/admin/clients/{jane.id}/activate').status_code == 409
        assert client.post(f'/admin/clients/{jane.id}/unlock').status_code == 409
        r = client.get(f'/admin/clients/{jane.id}/edit')
        assert r.status_code == 302
        assert _fresh(User, jane.id).active is False

        # Her real email address is free again.
        again = make_user(firstname='Jane', lastname='Smith', email='jane@example.com')
        assert again.id != jane.id

    def test_unticked_owner_keeps_account_but_not_the_dog(self, app, client, owner):
        jane = _client('Jane', 'Smith', email='jane@example.com')
        rex = _dog('Rex', jane)
        past = _booking(jane, rex, local_today() - datetime.timedelta(days=7))
        assert _delete_dog(client, rex).status_code == 200

        jane = _fresh(User, jane.id)
        assert jane.deleted_at is None and jane.active is True
        # The id-only helper applies the same deleted-dog filter as
        # get_accessible_dogs, so she can't act on the dog's old bookings.
        assert rex.id not in get_accessible_dog_ids(jane.id)
        assert not user_can_access_booking(jane, _fresh(Booking, past.id))
        jane_client = app.test_client()
        login(_as(jane_client), 'jane@example.com')
        r = jane_client.get('/profile')
        assert r.status_code == 200
        assert b'Deleted dog' not in r.data

        r = jane_client.post('/book', json={
            'date': (local_today() + datetime.timedelta(days=7)).isoformat(),
            'slot': 'Morning', 'dog_id': rex.id,
        })
        assert r.status_code == 400
        assert Booking.query.filter(Booking.dog_id == rex.id, Booking.date > local_today()).count() == 0

    def test_admin_cant_book_or_edit_a_deleted_dog(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        _walk_service()
        assert _delete_dog(client, rex).status_code == 200
        r = client.post('/admin/book_for_dog', json={
            'dog_id': rex.id, 'user_id': jane.id,
            'date': (local_today() + datetime.timedelta(days=7)).isoformat(), 'slot': 'Morning',
        })
        assert r.status_code == 404
        r = client.post(f'/admin/dogs/{rex.id}/update', json={'name': 'Rex', 'gender': 'male'})
        assert r.status_code == 404
        assert _fresh(Dog, rex.id).name.startswith('[Deleted dog')

    def test_create_booking_refuses_a_deleted_dog(self, app, owner):
        from app.services.booking_service import create_booking, DeletedDogError
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        rex.deleted_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()
        with pytest.raises(DeletedDogError):
            create_booking(dog=rex, user_id=jane.id, date=local_today(), slot='Morning',
                           service=_walk_service(), actor_id=owner.id, batch_id='x')

    def test_create_booking_reads_deleted_at_from_the_db(self, app, owner):
        """A delete committed elsewhere while this request holds a stale Dog
        object must still be seen — create_booking re-reads the row under
        FOR UPDATE (the same lock the delete route takes)."""
        from sqlalchemy import update
        from app.services.booking_service import create_booking, DeletedDogError
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        assert rex.deleted_at is None
        db.session.execute(update(Dog).where(Dog.id == rex.id)
                           .values(deleted_at=datetime.datetime(2026, 10, 1)),
                           execution_options={'synchronize_session': False})
        assert rex.deleted_at is None  # in-memory object is stale
        with pytest.raises(DeletedDogError):
            create_booking(dog=rex, user_id=jane.id, date=local_today(), slot='Morning',
                           service=_walk_service(), actor_id=owner.id, batch_id='x')

    def test_two_deleted_dogs_walked_in_the_same_slot(self, client, owner):
        jane = _client('Jane', 'Smith')
        sam = _client('Sam', 'Jones')
        rex, max_ = _dog('Rex', jane), _dog('Max', sam)
        day = local_today() - datetime.timedelta(days=5)
        _booking(jane, rex, day)
        _booking(sam, max_, day)
        assert _delete_dog(client, rex, owner_ids=[jane.id]).status_code == 200
        assert _delete_dog(client, max_, owner_ids=[sam.id]).status_code == 200
        assert Booking.query.filter_by(date=day, slot='Morning', status='confirmed').count() == 2


# ── billing is unchanged ─────────────────────────────────────────────────────

class TestBillingUnchanged:

    SEP = datetime.date(2026, 9, 1), datetime.date(2026, 10, 1)

    def _setup(self):
        db.session.add(PricingConfig(effective_from=datetime.date(2025, 1, 1), price_per_walk=15,
                                     price_per_drop_in=12, double_slot_discount=3, weekly_discount=1))
        jane = _client('Jane', 'Smith')
        tom = _client('Tom', 'Smith')
        rex = _dog('Rex', jane, tom)
        bella = _dog('Bella', jane)
        # Week of Mon 7 Sep: 5 walks for the household (weekly discount),
        # with a double on the Monday; Bella shares the Tuesday slot.
        for day, slot in [(7, 'Morning'), (7, 'Afternoon'), (8, 'Morning'), (9, 'Morning'), (10, 'Morning')]:
            _booking(jane, rex, datetime.date(2026, 9, day), slot)
        _booking(jane, bella, datetime.date(2026, 9, 8), 'Morning')
        _booking(jane, rex, datetime.date(2026, 9, 14), status='cancelled', cancelled_by='client',
                 bill_cancellation=True, cancelled_at=datetime.datetime(2026, 9, 13, 9))
        return jane, tom, rex, bella

    @staticmethod
    def _snapshot(user_id):
        from app.blueprints.admin.views.revenue import _revenue_for_range
        configs = PricingConfig.query.order_by(PricingConfig.effective_from.desc()).all()
        inv = invoice_for_client(user_id, *TestBillingUnchanged.SEP, configs)
        keys = ('total_walks', 'total_drop_ins', 'total_cancels', 'total_billable', 'doubles',
                'weekly_discount_total', 'weekly_discount_weeks', 'subtotal')
        daily, weekly = _revenue_for_range(datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))
        return ({k: inv[k] for k in keys},
                sorted(b.id for b in inv['all_billable']),
                [(r['date'], r['revenue']) for r in daily if r['revenue']], weekly)

    def test_unticked_owners(self, client, owner):
        jane, tom, rex, bella = self._setup()
        before = self._snapshot(jane.id)
        assert before[0]['doubles'] == 1 and before[0]['weekly_discount_weeks'] == 1
        assert _delete_dog(client, rex).status_code == 200
        assert self._snapshot(jane.id) == before

    def test_ticked_owners(self, client, owner):
        jane, tom, rex, bella = self._setup()
        before = self._snapshot(jane.id)
        # Jane still owns Bella, so only Tom (the co-owner) can go with Rex.
        assert _delete_dog(client, rex, owner_ids=[tom.id]).status_code == 200
        assert self._snapshot(jane.id) == before
        # Delete Bella and Jane too; the invoice is still identical.
        assert _delete_dog(client, _fresh(Dog, bella.id), owner_ids=[jane.id]).status_code == 200
        assert self._snapshot(jane.id) == before


# ── dogless "Delete client" ──────────────────────────────────────────────────

class TestDeleteDoglessClient:

    def test_button_only_for_a_client_with_no_dogs(self, client, owner):
        jane = _client('Jane', 'Smith')
        _dog('Rex', jane)
        dan = _client('Dan', 'Dogless')
        assert b'js-delete-client' not in client.get(f'/admin/clients/{jane.id}').data
        assert b'js-delete-client' in client.get(f'/admin/clients/{dan.id}').data
        assert b'js-delete-dog' in client.get(f'/admin/clients/{jane.id}').data

    def test_buttons_hidden_from_ordinary_admins(self, logged_in_admin):
        jane = _client('Jane', 'Smith')
        _dog('Rex', jane)
        dan = _client('Dan', 'Dogless')
        assert b'js-delete-dog' not in logged_in_admin.get(f'/admin/clients/{jane.id}').data
        assert b'js-delete-client' not in logged_in_admin.get(f'/admin/clients/{dan.id}').data

    def test_route_refuses_a_client_with_a_dog(self, client, owner):
        jane = _client('Jane', 'Smith')
        _dog('Rex', jane)
        r = client.post(f'/admin/clients/{jane.id}/delete', json={'typed_name': 'Jane Smith'})
        assert r.status_code == 400
        assert 'also owns Rex' in r.get_json()['message']
        assert _fresh(User, jane.id).deleted_at is None

    def test_route_refuses_walkers_and_self(self, client, owner):
        r = client.post(f'/admin/clients/{owner.id}/delete', json={'typed_name': 'Lydia Owner'})
        assert r.status_code in (400, 404)
        walker = make_user(firstname='Wally', lastname='Walker', role='walker')
        db.session.add(Client(user_id=walker.id, onboarding_completed=True))
        db.session.commit()
        r = client.post(f'/admin/clients/{walker.id}/delete', json={'typed_name': 'Wally Walker'})
        assert r.status_code == 400

    def test_deletes_a_dogless_client(self, client, owner):
        dan = _client('Dan', 'Dogless')
        assert client.post(f'/admin/clients/{dan.id}/delete', json={'typed_name': 'Dan'}).status_code == 400
        r = client.post(f'/admin/clients/{dan.id}/delete', json={'typed_name': 'dan dogless'})
        assert r.status_code == 200, r.get_json()
        dan = _fresh(User, dan.id)
        assert dan.deleted_at is not None and dan.full_name == f'[Deleted client #{dan.id}]'
        assert ActivityLog.query.filter_by(entity_type='client', action='removed').one().summary == \
            f'Deleted [Deleted client #{dan.id}]'

    def test_owner_of_only_deleted_dogs_counts_as_dogless(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        assert _delete_dog(client, rex).status_code == 200
        assert b'js-delete-client' in client.get(f'/admin/clients/{jane.id}').data
        r = client.post(f'/admin/clients/{jane.id}/delete', json={'typed_name': 'Jane Smith'})
        assert r.status_code == 200


# ── deleted records are hidden ───────────────────────────────────────────────

class TestHiding:

    def test_lists_hide_deleted_records(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        _dog('Bella', _client('Kim', 'Keep'))
        assert _delete_dog(client, rex, owner_ids=[jane.id]).status_code == 200

        clients_page = client.get('/admin/clients').data
        assert b'Deleted client' not in clients_page and b'Kim' in clients_page
        dogs_page = client.get('/admin/dogs').data
        assert b'Deleted dog' not in dogs_page and b'Bella' in dogs_page

    def test_deleted_client_page_is_read_only(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        _booking(jane, rex, local_today() - datetime.timedelta(days=3))
        assert _delete_dog(client, rex, owner_ids=[jane.id]).status_code == 200
        r = client.get(f'/admin/clients/{jane.id}')
        assert r.status_code == 200
        assert b'Deleted on' in r.data
        assert f'[Deleted dog #{rex.id}]'.encode() in r.data
        assert b'Edit Client' not in r.data and b'js-delete' not in r.data

    def test_unticked_owners_admin_page_hides_the_dog_card(self, client, owner):
        jane = _client('Jane', 'Smith')
        rex = _dog('Rex', jane)
        assert _delete_dog(client, rex).status_code == 200
        r = client.get(f'/admin/clients/{jane.id}')
        assert b'Deleted dog' not in r.data
        assert b'No dog added yet' in r.data
