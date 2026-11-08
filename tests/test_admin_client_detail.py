"""
/admin/clients/<id> and the add-dog validation-error re-render share one
context builder (clients.py::_client_detail_context) — these pin that the
error page shows exactly what the detail page shows, without per-dog queries.
"""
import datetime
from contextlib import contextmanager

from sqlalchemy import event

from app import db
from app.models import Client, Dog, DogOwner, Notification
from tests.conftest import login, make_user


@contextmanager
def _count_queries():
    statements = []

    def _capture(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(db.engine, 'before_cursor_execute', _capture)
    try:
        yield statements
    finally:
        event.remove(db.engine, 'before_cursor_execute', _capture)


def _client(email, firstname='Jane'):
    user = make_user(firstname=firstname, lastname='Smith', email=email)
    db.session.add(Client(user_id=user.id, onboarding_completed=True))
    db.session.commit()
    return user


def _dog(owner, name, role='primary', dob=None):
    dog = Dog(name=name, gender='female', date_of_birth=dob)
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=owner.id, role=role))
    db.session.commit()
    return dog


def _invalid_add_dog(client, client_id):
    """Missing dog name → AddDogForm fails validation → page re-rendered."""
    return client.post(f'/admin/clients/{client_id}/add-dog',
                       data={'dog_name': '', 'dog_gender': 'male'})


class TestAddDogValidationError:

    def test_rerenders_when_existing_dog_has_dob(self, client, admin_user):
        owner = _client('dob-owner@test.com')
        _dog(owner, 'Rex', dob=datetime.date(2020, 5, 1))
        login(client, admin_user.email)

        resp = _invalid_add_dog(client, owner.id)

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Rex' in html
        assert 'Dog name is required' in html
        # Modal re-opened so the admin sees the error.
        assert "getElementById('addDogModal')).show()" in html

    def test_shows_same_notifications_as_detail_page(self, client, admin_user):
        owner = _client('notif-owner@test.com')
        _dog(owner, 'Rex')
        for i in range(15):
            db.session.add(Notification(recipient_id=owner.id, notification_type='system',
                                        title=f'Notice number {i:02d}'))
        db.session.commit()
        login(client, admin_user.email)

        detail = client.get(f'/admin/clients/{owner.id}').get_data(as_text=True)
        error = _invalid_add_dog(client, owner.id).get_data(as_text=True)

        assert detail.count('Notice number') == 10
        assert error.count('Notice number') == 10

    def test_shows_same_owners_and_joinable_clients(self, client, admin_user):
        owner = _client('owner@test.com', firstname='Owner')
        rex = _dog(owner, 'Rex')
        co = _client('co@test.com', firstname='Coowner')
        db.session.add(DogOwner(dog_id=rex.id, user_id=co.id, role='secondary'))
        other = _client('other-household@test.com', firstname='Otherhouse')
        _dog(other, 'Bella')
        db.session.add(DogOwner(dog_id=Dog.query.filter_by(name='Bella').one().id,
                                user_id=owner.id, role='secondary'))
        db.session.commit()
        _client('joinable@test.com', firstname='Joinable')
        login(client, admin_user.email)

        detail = client.get(f'/admin/clients/{owner.id}').get_data(as_text=True)
        error = _invalid_add_dog(client, owner.id).get_data(as_text=True)

        for page in (detail, error):
            assert 'Coowner' in page       # secondary on Rex
            assert 'Bella' in page         # owner's secondary dog
            assert 'Otherhouse' in page    # Bella's primary
            assert 'Joinable' in page      # in the "link a client" picker

    def test_delete_button_follows_detail_page_rule(self, client):
        owner = make_user(firstname='Lydia', email='owner-sa@test.com', role='walker',
                          is_admin=True)
        owner.is_super_admin = True
        db.session.commit()
        dogless = _client('dogless@test.com')
        login(client, owner.email)

        detail = client.get(f'/admin/clients/{dogless.id}').get_data(as_text=True)
        error = _invalid_add_dog(client, dogless.id).get_data(as_text=True)

        assert 'js-delete-client' in detail
        assert 'js-delete-client' in error

    def test_query_count_does_not_grow_with_dogs(self, client, admin_user):
        one = _client('one-dog@test.com')
        solo = _dog(one, 'Solo')
        solo_co = _client('co-solo@test.com', firstname='CoSolo')
        db.session.add(DogOwner(dog_id=solo.id, user_id=solo_co.id, role='secondary'))
        many = _client('many-dogs@test.com')
        for name in ('A', 'B', 'C', 'D'):
            dog = _dog(many, f'Dog{name}')
            co = _client(f'co-{name}@test.com', firstname=f'Co{name}')
            db.session.add(DogOwner(dog_id=dog.id, user_id=co.id, role='secondary'))
        db.session.commit()
        login(client, admin_user.email)

        with _count_queries() as few:
            assert _invalid_add_dog(client, one.id).status_code == 200
        with _count_queries() as lots:
            assert _invalid_add_dog(client, many.id).status_code == 200

        assert len(lots) == len(few)
