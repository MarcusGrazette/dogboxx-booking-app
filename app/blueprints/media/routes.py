"""Authorized media — uploads too sensitive for the public /static tree.

Pickup-notes photos show key safes and door buzzers. They stay on disk in
uploads/pickup_notes/ (the Railway volume, R2 backup and reconcile-uploads all
assume that layout), but the public /static URL for that folder is blocked in
app/__init__.py::block_private_uploads and they are served only from here.
"""
import os

from flask import abort, current_app, send_from_directory
from flask_login import current_user, login_required

from . import media_bp
from app.models import Dog
from app.utils.booking_access import user_can_view_pickup_details


def pickup_notes_dir():
    """On-disk folder for pickup-notes photos — same path process_dog_photo
    writes to (UPLOAD_FOLDER's parent + 'pickup_notes')."""
    return os.path.join(os.path.dirname(current_app.config['UPLOAD_FOLDER']), 'pickup_notes')


@media_bp.route('/pickup-notes/<filename>')
@login_required
def pickup_photo(filename):
    """Serve a dog's pickup-notes photo to admins, active walkers and the
    dog's current owners. Everyone else gets 404 (not 403), so the response
    doesn't confirm the file exists. A replaced photo no longer matches any
    Dog row, so its old URL 404s too.

    Cache-Control is `private, no-store` (add_security_headers leaves it
    alone) and the URL is outside /static/, so the service worker never
    caches it either — a handed-off phone can't keep a copy past logout.
    """
    dog = Dog.query.filter_by(pickup_notes_photo=filename).first()
    if dog is None or not user_can_view_pickup_details(current_user, dog.id):
        abort(404)
    response = send_from_directory(pickup_notes_dir(), filename)
    response.headers['Cache-Control'] = 'private, no-store'
    return response
