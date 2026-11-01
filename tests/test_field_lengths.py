"""Form validation matches column limits (develop review #23).

- Client.street_address holds up to three newline-joined address lines of up
  to 200 characters each — it used to be VARCHAR(200), so two ordinary lines
  500'd onboarding and the admin client forms.
- Dog.breed is VARCHAR(100); every write path now rejects a longer value with
  a field error instead of failing at commit.

Postgres only: SQLite never enforced String(n), which is how this hid.
"""
from app import db
from app.models import Client, Dog, DogOwner, User
from tests.conftest import login, make_user

LINE = 'x' * 200          # the per-line maximum
LONG_BREED = 'b' * 101


def _onboarding_user():
    user = make_user(email='onboard@test.dogboxx.org')
    db.session.add(Client(user_id=user.id, onboarding_completed=False))
    db.session.commit()
    return user


def _client_with_two_dogs():
    user = make_user(firstname='Jane', lastname='Smith', email='jane@test.dogboxx.org')
    db.session.add(Client(user_id=user.id, onboarding_completed=True))
    dogs = [Dog(name='Rex', gender='male', breed='Lab'),
            Dog(name='Fido', gender='male', breed='Pug')]
    db.session.add_all(dogs)
    db.session.flush()
    db.session.add_all([DogOwner(dog_id=d.id, user_id=user.id, role='primary') for d in dogs])
    db.session.commit()
    return user, dogs


def _admin_form(**overrides):
    data = {'firstname': 'Jane', 'lastname': 'Smith', 'email': 'jane@test.dogboxx.org',
            'notify_email': 'y'}
    data.update(overrides)
    return data


class TestOnboarding:

    def test_three_full_address_lines_save(self, app, client):
        user = _onboarding_user()
        login(client, user.email)
        resp = client.post('/onboard', data={
            'address_line_1': LINE, 'address_line_2': LINE, 'address_line_3': LINE,
            'postcode': 'SW1A 1AA', 'dog_name': 'Buddy', 'dog_gender': 'male',
        })
        assert resp.status_code == 302
        saved = Client.query.filter_by(user_id=user.id).one().street_address
        assert saved == '\n'.join([LINE] * 3)

    def test_breed_over_100_chars_is_a_form_error(self, app, client):
        user = _onboarding_user()
        login(client, user.email)
        resp = client.post('/onboard', data={
            'address_line_1': '1 Test Street', 'postcode': 'SW1A 1AA',
            'dog_name': 'Buddy', 'dog_gender': 'male', 'dog_breed': LONG_BREED,
        })
        assert resp.status_code == 200  # form re-rendered, not a 500
        assert Dog.query.count() == 0


class TestAdminNewClient:

    def test_three_full_address_lines_save(self, app, logged_in_admin):
        resp = logged_in_admin.post('/admin/clients/new', data=_admin_form(
            address_line_1=LINE, address_line_2=LINE, address_line_3=LINE))
        assert resp.status_code == 302
        user = User.query.filter_by(email='jane@test.dogboxx.org').one()
        assert user.client.street_address == '\n'.join([LINE] * 3)

    def test_breed_over_100_chars_is_a_form_error(self, app, logged_in_admin):
        resp = logged_in_admin.post('/admin/clients/new', data=_admin_form(
            dog_name='Rex', dog_gender='male', dog_breed=LONG_BREED))
        assert resp.status_code == 200
        assert User.query.filter_by(email='jane@test.dogboxx.org').first() is None


class TestAdminEditClient:

    def test_three_full_address_lines_save(self, app, logged_in_admin):
        user, _ = _client_with_two_dogs()
        resp = logged_in_admin.post(f'/admin/clients/{user.id}/edit', data=_admin_form(
            address_line_1=LINE, address_line_2=LINE, address_line_3=LINE))
        assert resp.status_code == 302
        assert db.session.get(Client, user.client.id).street_address == '\n'.join([LINE] * 3)

    def test_extra_dog_breed_over_100_chars_saves_nothing(self, app, logged_in_admin):
        user, (_, extra) = _client_with_two_dogs()
        resp = logged_in_admin.post(f'/admin/clients/{user.id}/edit', data=_admin_form(
            firstname='Janet', dog_name='Rex', dog_gender='male',
            **{f'dog_name_{extra.id}': 'Fido', f'dog_breed_{extra.id}': LONG_BREED}))
        assert resp.status_code == 200
        assert b'Breed must be 100 characters or fewer' in resp.data
        assert db.session.get(User, user.id).firstname == 'Jane'
        assert db.session.get(Dog, extra.id).breed == 'Pug'

    def test_extra_dog_allergies_over_200_chars_saves_nothing(self, app, logged_in_admin):
        user, (_, extra) = _client_with_two_dogs()
        resp = logged_in_admin.post(f'/admin/clients/{user.id}/edit', data=_admin_form(
            dog_name='Rex', dog_gender='male',
            **{f'dog_name_{extra.id}': 'Fido', f'dog_allergies_{extra.id}': 'a' * 201}))
        assert resp.status_code == 200
        assert b'Allergies must be 200 characters or fewer' in resp.data
        assert db.session.get(Dog, extra.id).allergies is None


class TestAdminUpdateDog:

    def test_breed_over_100_chars_rejected_with_clear_message(self, app, logged_in_admin):
        dog = Dog(name='Rex', gender='male', breed='Lab')
        db.session.add(dog)
        db.session.commit()
        resp = logged_in_admin.post(f'/admin/dogs/{dog.id}/update', json={
            'name': 'Rex', 'gender': 'male', 'breed': LONG_BREED, 'allergies': '',
            'date_of_birth': '', 'whatsapp_group_url': '', 'hold_key': False,
        })
        assert resp.status_code == 400
        assert '100 characters' in resp.get_json()['message']
        assert db.session.get(Dog, dog.id).breed == 'Lab'

    def test_breed_of_exactly_100_chars_saves(self, app, logged_in_admin):
        dog = Dog(name='Rex', gender='male', breed='Lab')
        db.session.add(dog)
        db.session.commit()
        resp = logged_in_admin.post(f'/admin/dogs/{dog.id}/update', json={
            'name': 'Rex', 'gender': 'male', 'breed': 'b' * 100, 'allergies': '',
            'date_of_birth': '', 'whatsapp_group_url': '', 'hold_key': False,
        })
        assert resp.status_code == 200
        assert db.session.get(Dog, dog.id).breed == 'b' * 100
