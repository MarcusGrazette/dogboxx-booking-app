"""
Tests for rich-text pickup notes: HTML is sanitized on save via
/profile/update-pickup, and rendered safely (via |safe on already-sanitized
content) on the walker pickup list.
"""
from datetime import date

from app import db
from app.models import Booking, Dog, Walker
from app.utils.sanitize import sanitize_rich_text
from tests.conftest import login


class TestPickupNotesSanitizeOnSave:

    def test_html_formatting_is_preserved(self, app, client, client_user, dog):
        with app.app_context():
            email = client_user.email
            dog_id = dog.id

        login(client, email)
        resp = client.post('/profile/update-pickup', data={
            f'pickup_instructions_{dog_id}': '<h1>Gate code</h1><p><strong>1234</strong></p>',
            'notify_email': 'true',
        })
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

        with app.app_context():
            refreshed = db.session.get(Dog, dog_id)
            assert refreshed.pickup_instructions == '<h1>Gate code</h1><p><strong>1234</strong></p>'

    def test_script_tag_is_escaped_not_stored_live(self, app, client, client_user, dog):
        with app.app_context():
            email = client_user.email
            dog_id = dog.id

        login(client, email)
        client.post('/profile/update-pickup', data={
            f'pickup_instructions_{dog_id}': '<p>Hi</p><script>alert(1)</script>',
            'notify_email': 'true',
        })

        with app.app_context():
            refreshed = db.session.get(Dog, dog_id)
            assert '<script>' not in refreshed.pickup_instructions
            assert '&lt;script&gt;' in refreshed.pickup_instructions

    def test_empty_quill_markup_saves_as_none(self, app, client, client_user, dog):
        """An empty Quill editor's innerHTML is <p><br></p>, not '' — must
        still clear the field rather than storing throwaway markup."""
        with app.app_context():
            email = client_user.email
            dog_id = dog.id
            db.session.get(Dog, dog_id).pickup_instructions = 'Old notes'
            db.session.commit()

        login(client, email)
        client.post('/profile/update-pickup', data={
            f'pickup_instructions_{dog_id}': '<p><br></p>',
            'notify_email': 'true',
        })

        with app.app_context():
            refreshed = db.session.get(Dog, dog_id)
            assert refreshed.pickup_instructions is None


class TestPickupNotesSafeRendering:

    def test_sanitized_html_renders_formatted_and_script_stays_inert_on_pickup_list(
            self, app, client, walker_user, client_user, dog, service_type):
        """Writes through sanitize_rich_text directly (mirrors what every
        real write path — the route tested above included — guarantees)
        rather than storing raw HTML, since |safe rendering trusts the DB
        content to already be sanitized rather than re-checking at render
        time."""
        with app.app_context():
            walker = Walker.query.filter_by(user_id=walker_user.id).first()
            today = date.today()
            db.session.get(Dog, dog.id).pickup_instructions = sanitize_rich_text(
                '<h1>Gate</h1><p>Code <strong>1234</strong></p>'
                '<script>alert(1)</script>'
            )
            booking = Booking(
                user_id=client_user.id, dog_id=dog.id,
                service_type_id=service_type.id,
                date=today, slot='Morning',
                status='confirmed', walker_id=walker.id,
            )
            db.session.add(booking)
            db.session.commit()
            walker_email = walker_user.email

        login(client, walker_email)
        resp = client.get('/walker/pickups')
        assert resp.status_code == 200
        html = resp.data.decode()

        # Allowed formatting tags render as live markup.
        assert '<h1>Gate</h1>' in html
        assert '<strong>1234</strong>' in html
        # The script tag never renders as live, executable markup.
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


class TestAdminClientDetailPickupEditView:

    def test_no_pickup_instructions_does_not_render_literal_none(
            self, app, client, admin_user, client_user, dog):
        """Regression: the edit-view hidden textarea used
        `{{ dog.pickup_instructions if dog else '' }}` — that guards only on
        `dog` existing, not on pickup_instructions itself, so a dog with no
        notes yet (a very common state) rendered the literal text "None"
        into the Quill editor once opened for editing."""
        with app.app_context():
            assert dog.pickup_instructions is None
            admin_email = admin_user.email
            client_id = client_user.id

        login(client, admin_email)
        resp = client.get(f'/admin/clients/{client_id}')
        assert resp.status_code == 200
        html = resp.data.decode()

        edit_field_start = html.find('id="edit-pickup-instructions"')
        assert edit_field_start > 0
        field_snippet = html[edit_field_start:edit_field_start + 400]
        assert '>None<' not in field_snippet


class TestAdminDogsPickupPhotoUpload:
    """Regression: the admin dogs table's edit modal (opened via row click ->
    view modal -> Edit, the .view-edit-btn path) had no photo-upload control
    at all — only client /profile and admin_client_detail.html got one."""

    def test_upload_endpoint_saves_photo_and_returns_url(
            self, app, client, admin_user, dog):
        with app.app_context():
            admin_email = admin_user.email
            dog_id = dog.id

        login(client, admin_email)
        with open('app/static/uploads/dogs/default-dog.png', 'rb') as f:
            resp = client.post(
                f'/admin/dogs/{dog_id}/pickup-photo',
                data={'file': (f, 'photo.png')},
                content_type='multipart/form-data',
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert 'pickup_notes' in data['url']

        with app.app_context():
            refreshed = db.session.get(Dog, dog_id)
            assert refreshed.pickup_notes_photo is not None
            assert refreshed.pickup_notes_photo in data['url']

    def test_upload_endpoint_requires_admin(self, app, client, client_user, dog):
        with app.app_context():
            email = client_user.email
            dog_id = dog.id

        login(client, email)
        with open('app/static/uploads/dogs/default-dog.png', 'rb') as f:
            resp = client.post(
                f'/admin/dogs/{dog_id}/pickup-photo',
                data={'file': (f, 'photo.png')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
        assert resp.status_code in (302, 403)

    def test_upload_endpoint_rejects_unknown_dog(self, app, client, admin_user):
        with app.app_context():
            admin_email = admin_user.email

        login(client, admin_email)
        with open('app/static/uploads/dogs/default-dog.png', 'rb') as f:
            resp = client.post(
                '/admin/dogs/999999/pickup-photo',
                data={'file': (f, 'photo.png')},
                content_type='multipart/form-data',
            )
        assert resp.status_code == 404

    def test_edit_modal_view_edit_btn_carries_photo_url_dataset(
            self, app, client, admin_user, dog):
        """The .view-edit-btn (the real, working edit entry point) must carry
        data-dog-pickup-photo-url so the edit modal's photo thumbnail can be
        populated when reopened — the edit modal itself has no server-side
        knowledge of which dog is being edited otherwise."""
        with app.app_context():
            d = db.session.get(Dog, dog.id)
            d.pickup_notes_photo = 'abc123.png'
            db.session.commit()
            admin_email = admin_user.email

        login(client, admin_email)
        resp = client.get('/admin/dogs')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-dog-pickup-photo-url="/static/uploads/pickup_notes/abc123.png"' in html
