"""
Replacing a photo deletes the previous file and its R2 backup copy
(app/utils/uploads.py::delete_replaced_photo), at every route that writes a
photo column — profile_pic, Dog.pic and Dog.pickup_notes_photo.

The R2 copy matters as much as the local file: scripts/start.sh restores every
R2 object onto an empty volume, so a photo deleted only locally would come back
after a disaster-recovery restore.
"""
import io
import os
import types

import pytest
from PIL import Image

from app import db
from app.models import Client, Dog, DogOwner, User
from app.utils import uploads as uploads_mod

from tests.conftest import login, make_user


def _png():
    buf = io.BytesIO()
    Image.new('RGB', (32, 32), color='blue').save(buf, 'PNG')
    buf.seek(0)
    return buf


def _folder(app, subfolder):
    base = os.path.dirname(app.config['UPLOAD_FOLDER'])
    path = os.path.join(base, subfolder)
    os.makedirs(path, exist_ok=True)
    return path


def _place_file(app, subfolder, filename):
    path = os.path.join(_folder(app, subfolder), filename)
    with open(path, 'wb') as f:
        f.write(b'old photo')
    return path


@pytest.fixture
def spawned(monkeypatch):
    """Record gevent.spawn calls made by the uploads module instead of
    running them, so tests can assert the R2 delete was queued."""
    calls = []
    monkeypatch.setattr(
        uploads_mod, 'gevent',
        types.SimpleNamespace(spawn=lambda fn, *args: calls.append((fn, args))),
    )
    return calls


def _r2_deletes(spawned):
    return [args[0] for fn, args in spawned if fn is uploads_mod._delete_from_r2]


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------

class TestDeleteReplacedPhoto:

    def test_deletes_local_file_and_queues_r2_delete(self, app, spawned):
        path = _place_file(app, 'dogs', 'old-uuid.jpg')
        with app.app_context():
            uploads_mod.delete_replaced_photo('old-uuid.jpg', 'dogs')
        assert not os.path.exists(path)
        assert _r2_deletes(spawned) == ['dogs/old-uuid.jpg']

    def test_missing_file_still_clears_r2_copy(self, app, spawned):
        with app.app_context():
            uploads_mod.delete_replaced_photo('never-existed.jpg', 'profiles')
        assert _r2_deletes(spawned) == ['profiles/never-existed.jpg']

    @pytest.mark.parametrize('filename', [None, ''])
    def test_no_previous_photo_is_a_no_op(self, app, spawned, filename):
        with app.app_context():
            uploads_mod.delete_replaced_photo(filename, 'dogs')
        assert spawned == []

    def test_bundled_default_image_is_never_deleted(self, app, spawned):
        path = _place_file(app, 'dogs', 'default-dog.png')
        with app.app_context():
            uploads_mod.delete_replaced_photo('default-dog.png', 'dogs')
        assert os.path.exists(path)
        assert spawned == []

    @pytest.mark.parametrize('filename', ['../profiles/victim.jpg', '/etc/passwd'])
    def test_path_outside_its_folder_is_refused(self, app, spawned, filename):
        victim = _place_file(app, 'profiles', 'victim.jpg')
        with app.app_context():
            uploads_mod.delete_replaced_photo(filename, 'dogs')
        assert os.path.exists(victim)
        assert spawned == []

    @pytest.mark.parametrize('filename', ['bad\x00name.jpg', '\x00'])
    def test_malformed_name_never_raises(self, app, spawned, filename):
        """realpath()/remove() raise ValueError (not OSError) on an embedded
        NUL; that must not escape — every caller would turn an already
        committed upload into a 500."""
        with app.app_context():
            uploads_mod.delete_replaced_photo(filename, 'dogs')  # must not raise
        assert spawned == []


class TestBackupToR2:

    def test_uploads_the_file_when_configured(self, monkeypatch):
        """Pins _backup_to_r2 after the _r2_client() extraction."""
        uploaded = []
        fake_client = types.SimpleNamespace(
            upload_file=lambda *args: uploaded.append(args))
        monkeypatch.setattr(uploads_mod, '_r2_client',
                            lambda: (fake_client, 'bucket'))
        uploads_mod._backup_to_r2('/vol/dogs/new.jpg', 'dogs/new.jpg')
        assert uploaded == [('/vol/dogs/new.jpg', 'bucket', 'dogs/new.jpg')]

    def test_unconfigured_is_a_no_op(self, monkeypatch):
        for var in ('R2_ENDPOINT_URL', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY'):
            monkeypatch.delenv(var, raising=False)
        uploads_mod._backup_to_r2('/vol/dogs/new.jpg', 'dogs/new.jpg')  # must not raise


class TestDeleteFromR2:

    def test_deletes_the_object_when_configured(self, monkeypatch):
        deleted = []
        fake_client = types.SimpleNamespace(
            delete_object=lambda **kw: deleted.append(kw))
        monkeypatch.setattr(uploads_mod, '_r2_client',
                            lambda: (fake_client, 'bucket'))
        uploads_mod._delete_from_r2('dogs/old.jpg')
        assert deleted == [{'Bucket': 'bucket', 'Key': 'dogs/old.jpg'}]

    def test_unconfigured_is_a_no_op(self, monkeypatch):
        for var in ('R2_ENDPOINT_URL', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY'):
            monkeypatch.delenv(var, raising=False)
        uploads_mod._delete_from_r2('dogs/old.jpg')  # must not raise

    def test_r2_failure_never_raises(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError('R2 down')
        monkeypatch.setattr(uploads_mod, '_r2_client',
                            lambda: (types.SimpleNamespace(delete_object=boom), 'b'))
        uploads_mod._delete_from_r2('dogs/old.jpg')  # must not raise


# ---------------------------------------------------------------------------
# Every route that replaces a photo
# ---------------------------------------------------------------------------

def _post_file(flask_client, url):
    return flask_client.post(url, data={'file': (_png(), 'photo.png')},
                             content_type='multipart/form-data')


def _assert_replaced(app, spawned, subfolder, old_path, new_filename):
    assert not os.path.exists(old_path), 'old photo file was not deleted'
    assert os.path.exists(os.path.join(_folder(app, subfolder), new_filename))
    assert _r2_deletes(spawned) == [f'{subfolder}/{os.path.basename(old_path)}']


class TestRoutesDeleteThePreviousPhoto:
    """Setup writes go through the autouse fixture's session, not a nested
    app_context(): test-client requests reuse that session, so a write made
    in a separate context leaves it holding the stale, photo-less row."""

    def test_client_dog_photo(self, app, logged_in_client, dog, spawned):
        old = _place_file(app, 'dogs', 'old-dog.jpg')
        db.session.get(Dog, dog.id).pic = 'old-dog.jpg'
        db.session.commit()

        resp = _post_file(logged_in_client, '/profile/upload-dog-photo')
        assert resp.status_code == 200, resp.get_json()

        new = db.session.get(Dog, dog.id).pic
        _assert_replaced(app, spawned, 'dogs', old, new)

    def test_client_profile_photo(self, app, logged_in_client, client_user, spawned):
        old = _place_file(app, 'profiles', 'old-me.jpg')
        db.session.get(User, client_user.id).profile_pic = 'old-me.jpg'
        db.session.commit()

        resp = _post_file(logged_in_client, '/profile/upload-profile-photo')
        assert resp.status_code == 200, resp.get_json()

        new = db.session.get(User, client_user.id).profile_pic
        _assert_replaced(app, spawned, 'profiles', old, new)

    def test_client_pickup_photo(self, app, logged_in_client, dog, spawned):
        old = _place_file(app, 'pickup_notes', 'old-keysafe.png')
        db.session.get(Dog, dog.id).pickup_notes_photo = 'old-keysafe.png'
        db.session.commit()

        resp = _post_file(logged_in_client, f'/profile/dog/{dog.id}/pickup-photo')
        assert resp.status_code == 200, resp.get_json()

        new = db.session.get(Dog, dog.id).pickup_notes_photo
        _assert_replaced(app, spawned, 'pickup_notes', old, new)

    def test_walker_profile_photo(self, app, logged_in_walker, walker_user, spawned):
        old = _place_file(app, 'profiles', 'old-walker.jpg')
        db.session.get(User, walker_user.id).profile_pic = 'old-walker.jpg'
        db.session.commit()

        resp = _post_file(logged_in_walker, '/walker/profile/upload-photo')
        assert resp.status_code == 200, resp.get_json()

        new = db.session.get(User, walker_user.id).profile_pic
        _assert_replaced(app, spawned, 'profiles', old, new)

    def test_admin_client_pickup_photo(self, app, logged_in_admin, client_user, dog, spawned):
        old = _place_file(app, 'pickup_notes', 'old-admin-client.png')
        db.session.get(Dog, dog.id).pickup_notes_photo = 'old-admin-client.png'
        db.session.commit()

        resp = _post_file(logged_in_admin, f'/admin/clients/{client_user.id}/pickup-photo')
        assert resp.status_code == 200, resp.get_json()

        new = db.session.get(Dog, dog.id).pickup_notes_photo
        _assert_replaced(app, spawned, 'pickup_notes', old, new)

    def test_admin_dog_pickup_photo(self, app, logged_in_admin, dog, spawned):
        old = _place_file(app, 'pickup_notes', 'old-admin-dog.png')
        db.session.get(Dog, dog.id).pickup_notes_photo = 'old-admin-dog.png'
        db.session.commit()

        resp = _post_file(logged_in_admin, f'/admin/dogs/{dog.id}/pickup-photo')
        assert resp.status_code == 200, resp.get_json()

        new = db.session.get(Dog, dog.id).pickup_notes_photo
        _assert_replaced(app, spawned, 'pickup_notes', old, new)

    def test_onboarding_replaces_admin_created_dog_photo(self, app, client, spawned):
        old = _place_file(app, 'dogs', 'old-onboard.jpg')
        with app.app_context():
            user = make_user(email='onboard-photo@test.dogboxx.org')
            db.session.add(Client(user_id=user.id, onboarding_completed=False))
            d = Dog(name='Rex', pic='old-onboard.jpg')
            db.session.add(d)
            db.session.flush()
            db.session.add(DogOwner(dog_id=d.id, user_id=user.id, role='primary'))
            db.session.commit()
            dog_id, email = d.id, user.email

        login(client, email)
        resp = client.post('/onboard', data={
            'address_line_1': '1 Test Street',
            'postcode': 'SW1A 1AA',
            'dog_name': 'Rex',
            'dog_gender': 'male',
            'file': (_png(), 'rex.png'),
        }, content_type='multipart/form-data')
        assert resp.status_code == 302, resp.get_data(as_text=True)

        new = db.session.get(Dog, dog_id).pic
        _assert_replaced(app, spawned, 'dogs', old, new)

    def test_first_photo_deletes_nothing(self, app, logged_in_client, dog, spawned):
        resp = _post_file(logged_in_client, '/profile/upload-dog-photo')
        assert resp.status_code == 200, resp.get_json()
        assert _r2_deletes(spawned) == []


def _fail(*args, **kwargs):
    raise RuntimeError('simulated failure')


class TestFailedSave:

    def test_keeps_old_photo_and_discards_the_unsaved_new_one(
            self, app, logged_in_client, dog, spawned, monkeypatch):
        """If the commit fails the column still points at the old file, so it
        must stay; the new file was written before the commit and is now
        referenced by nothing, so it (and its R2 copy) goes."""
        old = _place_file(app, 'pickup_notes', 'keep-me.png')
        db.session.get(Dog, dog.id).pickup_notes_photo = 'keep-me.png'
        db.session.commit()
        before = set(os.listdir(_folder(app, 'pickup_notes')))

        monkeypatch.setattr('app.blueprints.client.views.profile.record_admin_action', _fail)
        resp = _post_file(logged_in_client, f'/profile/dog/{dog.id}/pickup-photo')
        assert resp.status_code == 500

        assert os.path.exists(old)
        assert db.session.get(Dog, dog.id).pickup_notes_photo == 'keep-me.png'
        assert set(os.listdir(_folder(app, 'pickup_notes'))) == before, \
            'the unsaved new file was left on disk'
        discarded = _r2_deletes(spawned)
        assert len(discarded) == 1 and discarded[0] != 'pickup_notes/keep-me.png'

    def test_error_after_the_commit_keeps_the_new_photo(
            self, app, logged_in_client, dog, spawned, monkeypatch):
        """An exception after a successful commit (here url_for) must not
        discard the photo the row now points at — the `committed` guard."""
        monkeypatch.setattr('app.blueprints.client.views.profile.url_for', _fail)
        resp = _post_file(logged_in_client, f'/profile/dog/{dog.id}/pickup-photo')
        assert resp.status_code == 500

        new = db.session.get(Dog, dog.id).pickup_notes_photo
        assert new is not None
        assert os.path.exists(os.path.join(_folder(app, 'pickup_notes'), new))
        assert f'pickup_notes/{new}' not in _r2_deletes(spawned)

    def test_onboarding_discards_the_unsaved_new_photo(self, app, client, spawned,
                                                       monkeypatch):
        with app.app_context():
            user = make_user(email='onboard-fail@test.dogboxx.org')
            db.session.add(Client(user_id=user.id, onboarding_completed=False))
            db.session.commit()
            email = user.email
        login(client, email)
        before = set(os.listdir(_folder(app, 'dogs')))

        monkeypatch.setattr(
            'app.blueprints.client.views.onboarding.clean_rich_text_or_none', _fail)
        client.post('/onboard', data={
            'address_line_1': '1 Test Street',
            'postcode': 'SW1A 1AA',
            'dog_name': 'Rex',
            'dog_gender': 'male',
            'file': (_png(), 'rex.png'),
        }, content_type='multipart/form-data')

        assert set(os.listdir(_folder(app, 'dogs'))) == before, \
            'the unsaved new file was left on disk'
        assert len(_r2_deletes(spawned)) == 1
