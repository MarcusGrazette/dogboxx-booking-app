"""Pickup-notes photos (key safes, buzzers) are served only to people allowed
to see them, never from the public /static tree, and never cached.

Regression for develop review #5.
"""
import os
import uuid

import pytest

from app import db as _db
from app.blueprints.media.routes import pickup_notes_dir
from app.models import Client, Dog, DogOwner
from tests.conftest import login, make_user


@pytest.fixture
def photo_dog(app, dog):
    """The `dog` fixture with a pickup-notes photo on disk."""
    filename = f'{uuid.uuid4()}.png'
    folder = pickup_notes_dir()
    os.makedirs(folder, exist_ok=True)
    with open('app/static/uploads/dogs/default-dog.png', 'rb') as src, \
            open(os.path.join(folder, filename), 'wb') as dst:
        dst.write(src.read())
    dog.pickup_notes_photo = filename
    _db.session.commit()
    return dog


def _url(dog):
    return f'/media/pickup-notes/{dog.pickup_notes_photo}'


def _onboarded_client(**kwargs):
    user = make_user(role='client', **kwargs)
    _db.session.add(Client(user_id=user.id, onboarding_completed=True))
    _db.session.commit()
    return user


def _assert_served(resp):
    assert resp.status_code == 200
    assert resp.mimetype == 'image/png'
    assert resp.headers['Cache-Control'] == 'private, no-store'


class TestAllowed:
    def test_primary_owner(self, app, client, client_user, photo_dog):
        login(client, client_user.email)
        _assert_served(client.get(_url(photo_dog)))

    def test_co_owner(self, app, client, photo_dog):
        co = _onboarded_client()
        _db.session.add(DogOwner(dog_id=photo_dog.id, user_id=co.id, role='secondary'))
        _db.session.commit()
        login(client, co.email)
        _assert_served(client.get(_url(photo_dog)))

    def test_walker(self, app, client, walker_user, photo_dog):
        login(client, walker_user.email)
        _assert_served(client.get(_url(photo_dog)))

    def test_admin(self, app, client, admin_user, photo_dog):
        login(client, admin_user.email)
        _assert_served(client.get(_url(photo_dog)))


class TestDenied:
    def test_logged_out_redirects_to_login(self, app, client, photo_dog):
        resp = client.get(_url(photo_dog))
        assert resp.status_code == 302
        assert '/auth/login' in resp.headers['Location']

    def test_after_logout(self, app, client, client_user, photo_dog):
        login(client, client_user.email)
        assert client.get(_url(photo_dog)).status_code == 200
        client.post('/auth/logout')
        resp = client.get(_url(photo_dog))
        assert resp.status_code == 302
        assert '/auth/login' in resp.headers['Location']

    def test_other_client(self, app, client, photo_dog):
        other = _onboarded_client()
        login(client, other.email)
        assert client.get(_url(photo_dog)).status_code == 404

    def test_revoked_co_owner(self, app, client, photo_dog):
        co = _onboarded_client()
        link = DogOwner(dog_id=photo_dog.id, user_id=co.id, role='secondary')
        _db.session.add(link)
        _db.session.commit()
        _db.session.delete(link)
        _db.session.commit()
        login(client, co.email)
        assert client.get(_url(photo_dog)).status_code == 404

    def test_demoted_walker(self, app, client, photo_dog):
        """remove_walker_role leaves the user active with role='client'."""
        former = _onboarded_client()
        login(client, former.email)
        assert client.get(_url(photo_dog)).status_code == 404

    def test_replaced_photo_url_is_dead(self, app, client, client_user, photo_dog):
        old_url = _url(photo_dog)
        photo_dog.pickup_notes_photo = f'{uuid.uuid4()}.png'
        _db.session.commit()
        login(client, client_user.email)
        assert client.get(old_url).status_code == 404

    def test_unknown_filename(self, app, client, admin_user, photo_dog):
        login(client, admin_user.email)
        assert client.get('/media/pickup-notes/nope.png').status_code == 404


class TestPublicStaticUrlBlocked:
    """The old /static URL 404s for everyone — even an admin — so privacy
    can't silently depend on who happens to be logged in."""

    @pytest.mark.parametrize('path', [
        'uploads/pickup_notes/{f}',
        'uploads//pickup_notes/{f}',
        'uploads/./pickup_notes/{f}',
        'uploads/dogs/../pickup_notes/{f}',
    ])
    def test_static_url_404s(self, app, client, admin_user, photo_dog, path):
        login(client, admin_user.email)
        url = '/static/' + path.format(f=photo_dog.pickup_notes_photo)
        assert client.get(url).status_code == 404

    def test_static_url_404s_anonymous(self, app, client, photo_dog):
        url = f'/static/uploads/pickup_notes/{photo_dog.pickup_notes_photo}'
        assert client.get(url).status_code == 404

    def test_dog_photos_still_public(self, app, client):
        resp = client.get('/static/uploads/dogs/default-dog.png')
        assert resp.status_code == 200
        assert resp.headers['Cache-Control'] == 'public, max-age=3600'


class TestUploadReturnsPrivateUrl:
    def test_client_upload_url(self, app, client, client_user, dog):
        login(client, client_user.email)
        with open('app/static/uploads/dogs/default-dog.png', 'rb') as f:
            resp = client.post(
                f'/profile/dog/{dog.id}/pickup-photo',
                data={'file': (f, 'keysafe.png')},
                content_type='multipart/form-data',
            )
        data = resp.get_json()
        assert data['success'] is True
        assert data['url'].startswith('/media/pickup-notes/')
        _assert_served(client.get(data['url']))
