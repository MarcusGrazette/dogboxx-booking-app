"""
The walker pickup card's address, Map link and phone come from the dog's
primary owner — never from booking.user, which is just whoever clicked Book
(possibly a co-owner at another address, or with no Client record at all).
Asserts on the rendered card for both routes that render it.
"""
import re
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import event

from app import db
from app.models import Booking, Client, Dog, DogOwner, Walker
from tests.conftest import login, make_user

PICKUP_DATE = date(2026, 10, 7)  # a Wednesday
DATE_STR = PICKUP_DATE.isoformat()

PRIMARY_ADDR = '1 Primary Road'
PRIMARY_MAPS = 'https://maps.example.com/primary'
PRIMARY_PHONE = '07700 900001'
SECONDARY_ADDR = '9 Secondary Street'
SECONDARY_MAPS = 'https://maps.example.com/secondary'
SECONDARY_PHONE = '07700 900009'


def _pickup_urls():
    return [f'/walker/pickups/{DATE_STR}', f'/walker/api/pickup-list/{DATE_STR}']


@pytest.fixture
def household(app, walker_user, service_type):
    """A dog with a primary owner and a secondary co-owner at different
    addresses, plus the walker it's assigned to. Returns a dict of ids."""
    primary = make_user(firstname='Prima', lastname='Owner')
    primary.phone = PRIMARY_PHONE
    db.session.add(Client(user_id=primary.id, onboarding_completed=True,
                          street_address=PRIMARY_ADDR, postal_code='aa1 1aa',
                          maps_url=PRIMARY_MAPS))
    secondary = make_user(firstname='Secunda', lastname='Owner')
    secondary.phone = SECONDARY_PHONE
    dog = Dog(name='Rex')
    db.session.add(dog)
    db.session.flush()
    db.session.add_all([
        DogOwner(dog_id=dog.id, user_id=primary.id, role='primary'),
        DogOwner(dog_id=dog.id, user_id=secondary.id, role='secondary'),
    ])
    db.session.commit()
    walker = Walker.query.filter_by(user_id=walker_user.id).first()
    return dict(primary_id=primary.id, secondary_id=secondary.id,
                dog_id=dog.id, walker_id=walker.id,
                service_type_id=service_type.id, walker_email=walker_user.email)


def _book(h, user_id, dog_id=None, slot='Morning'):
    db.session.add(Booking(
        user_id=user_id, dog_id=dog_id or h['dog_id'],
        service_type_id=h['service_type_id'], date=PICKUP_DATE, slot=slot,
        status='confirmed', walker_id=h['walker_id'],
    ))
    db.session.commit()


def _give_secondary_an_address(h):
    db.session.add(Client(user_id=h['secondary_id'], onboarding_completed=True,
                          street_address=SECONDARY_ADDR, postal_code='zz9 9zz',
                          maps_url=SECONDARY_MAPS))
    db.session.commit()


def _assert_primary_contact(html):
    assert PRIMARY_ADDR in html
    assert 'AA1 1AA' in html
    assert PRIMARY_MAPS in html
    assert 'wa.me/447700900001' in html
    assert SECONDARY_ADDR not in html
    assert SECONDARY_MAPS not in html
    assert '447700900009' not in html


class TestPickupCardUsesPrimaryOwner:

    @pytest.mark.parametrize('url', _pickup_urls())
    def test_secondary_booker_at_other_address_shows_primary(
            self, app, client, household, url):
        _give_secondary_an_address(household)
        _book(household, household['secondary_id'])

        login(client, household['walker_email'])
        resp = client.get(url)
        assert resp.status_code == 200
        _assert_primary_contact(resp.data.decode())

    @pytest.mark.parametrize('url', _pickup_urls())
    def test_secondary_booker_without_client_record_still_shows_address(
            self, app, client, household, url):
        """Before the fix this card showed no address and no Map button."""
        _book(household, household['secondary_id'])

        login(client, household['walker_email'])
        resp = client.get(url)
        assert resp.status_code == 200
        _assert_primary_contact(resp.data.decode())

    def test_primary_booker_unchanged(self, app, client, household):
        _give_secondary_an_address(household)
        _book(household, household['primary_id'])

        login(client, household['walker_email'])
        resp = client.get(f'/walker/pickups/{DATE_STR}')
        _assert_primary_contact(resp.data.decode())

    def test_dog_without_primary_owner_falls_back_to_booker(
            self, app, client, household):
        _give_secondary_an_address(household)
        DogOwner.query.filter_by(dog_id=household['dog_id'], role='primary').delete()
        db.session.commit()
        _book(household, household['secondary_id'])

        login(client, household['walker_email'])
        html = client.get(f'/walker/pickups/{DATE_STR}').data.decode()
        assert SECONDARY_ADDR in html
        assert SECONDARY_MAPS in html


@contextmanager
def _primary_owner_lookups():
    """Capture the batched primary-owner lookup (filters dog_owners.dog_id IN …)."""
    hits = []

    def _capture(conn, cursor, statement, params, context, executemany):
        if re.search(r'dog_owners\.dog_id IN', statement):
            hits.append(statement)

    event.listen(db.engine, 'before_cursor_execute', _capture)
    try:
        yield hits
    finally:
        event.remove(db.engine, 'before_cursor_execute', _capture)


class TestPrimaryOwnerLookupIsBatched:

    def test_one_lookup_regardless_of_card_count(self, app, client, household):
        for name in ('Fido', 'Bella', 'Max'):
            extra = Dog(name=name)
            db.session.add(extra)
            db.session.flush()
            db.session.add(DogOwner(dog_id=extra.id, user_id=household['primary_id'],
                                    role='primary'))
            db.session.commit()
            _book(household, household['primary_id'], dog_id=extra.id)
        _book(household, household['secondary_id'])

        login(client, household['walker_email'])
        with _primary_owner_lookups() as hits:
            resp = client.get(f'/walker/pickups/{DATE_STR}')
        assert resp.status_code == 200
        assert len(hits) == 1
