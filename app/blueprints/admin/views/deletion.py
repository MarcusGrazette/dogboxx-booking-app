"""Super-admin client & dog deletion (FEATURES #100).

Dog-first: deleting a dog can take any of its owners with it; a client can
otherwise only be deleted once they own no (live) dogs. Everything is
anonymised in place — see app/utils/deletion.py for why rows are never
removed or repointed. The browser's greyed-out tick boxes are only a hint:
the POST routes repeat every check.
"""
import logging

from flask import request, jsonify
from flask_login import login_required, current_user

from app import db
from app.blueprints.admin import admin_bp
from app.models import Dog, User
from app.utils.activity_log import record_admin_action
from app.utils.decorators import admin_required
from app.utils.deletion import (
    anonymise_client, anonymise_dog, client_block_reason, delete_photos,
    deleted_client_label, deleted_dog_label, dog_owners, future_bookings_for_dog,
)


def _forbidden():
    return jsonify(success=False, message="Only the business owner can delete clients and dogs."), 403


def _names_match(typed, expected):
    return (typed or '').strip().casefold() == (expected or '').strip().casefold()


def _live_dog_or_none(dog_id, *, lock=False):
    dog = db.session.get(Dog, dog_id, with_for_update=lock, populate_existing=lock)
    return dog if dog and dog.deleted_at is None else None


@admin_bp.route("/dogs/<int:dog_id>/delete-preview")
@login_required
@admin_required
def dog_delete_preview(dog_id):
    """What deleting this dog would involve. No side effects."""
    if not current_user.is_super_admin:
        return _forbidden()
    dog = _live_dog_or_none(dog_id)
    if not dog:
        return jsonify(success=False, message="Dog not found"), 404

    future = [
        {
            'date': b.date.strftime('%a %-d %b %Y'),
            'slot': b.slot,
            'service': b.service_type.name if b.service_type else '',
            'status': b.status,
        }
        for b in future_bookings_for_dog(dog.id)
    ]
    owners = []
    for ownership, user in dog_owners(dog.id):
        reason = client_block_reason(user, current_user, exclude_dog_id=dog.id)
        owners.append({
            'id': user.id,
            'name': user.full_name,
            'role': ownership.role,
            'can_delete': reason is None,
            'reason': f"{user.firstname} {reason}" if reason else None,
        })
    return jsonify(success=True, dog={'id': dog.id, 'name': dog.name},
                   future_bookings=future, owners=owners)


@admin_bp.route("/dogs/<int:dog_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_dog(dog_id):
    """Anonymise a dog and any ticked owners, in one transaction.

    Expects JSON: { "owner_ids": [int, ...], "typed_name": str }
    """
    if not current_user.is_super_admin:
        return _forbidden()
    data = request.get_json(silent=True) or {}
    try:
        owner_ids = {int(i) for i in (data.get('owner_ids') or [])}
    except (TypeError, ValueError):
        return jsonify(success=False, message="Invalid owner list"), 400

    try:
        # Row lock so two deletes (or a delete and an edit) can't interleave.
        dog = _live_dog_or_none(dog_id, lock=True)
        if not dog:
            db.session.rollback()
            return jsonify(success=False, message="Dog not found"), 404
        if not _names_match(data.get('typed_name'), dog.name):
            db.session.rollback()
            return jsonify(success=False, message=f"Type {dog.name}'s name to confirm."), 400
        if future_bookings_for_dog(dog.id):
            db.session.rollback()
            return jsonify(success=False, message=(
                f"{dog.name} still has upcoming bookings. Cancel them first."
            )), 409

        owners_by_id = {u.id: u for _, u in dog_owners(dog.id)}
        if not owner_ids <= owners_by_id.keys():
            db.session.rollback()
            return jsonify(success=False, message="That person doesn't own this dog."), 400
        to_delete = [owners_by_id[i] for i in sorted(owner_ids)]
        for user in to_delete:
            reason = client_block_reason(user, current_user, exclude_dog_id=dog.id)
            if reason:
                db.session.rollback()
                return jsonify(success=False, message=f"Can't delete: {user.full_name} {reason}."), 400

        photos = anonymise_dog(dog)
        for user in to_delete:
            photos += anonymise_client(user)

        labels = [deleted_dog_label(dog.id)] + [deleted_client_label(u.id) for u in to_delete]
        record_admin_action(
            'dog', dog.id, 'removed', actor_id=current_user.id,
            summary="Deleted " + _join(labels),
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error deleting dog {dog_id}: {e}")
        return jsonify(success=False, message="Something went wrong — nothing was deleted."), 500

    delete_photos(photos)
    logging.info(
        f"Super-admin {current_user.id} deleted dog {dog_id} "
        f"and clients {[u.id for u in to_delete]}"
    )
    return jsonify(success=True, message=f"Deleted {_join(labels)}.")


@admin_bp.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_client(client_id):
    """Anonymise a client who owns no dogs.

    Expects JSON: { "typed_name": str }
    """
    if not current_user.is_super_admin:
        return _forbidden()
    data = request.get_json(silent=True) or {}
    try:
        user = db.session.get(User, client_id, with_for_update=True, populate_existing=True)
        if not user or user.client is None:
            db.session.rollback()
            return jsonify(success=False, message="Client not found"), 404
        reason = client_block_reason(user, current_user)
        if reason:
            db.session.rollback()
            return jsonify(success=False, message=f"Can't delete: {user.full_name} {reason}."), 400
        if not _names_match(data.get('typed_name'), user.full_name):
            db.session.rollback()
            return jsonify(success=False, message=f"Type {user.full_name}'s name to confirm."), 400

        photos = anonymise_client(user)
        label = deleted_client_label(user.id)
        record_admin_action(
            'client', user.id, 'removed', actor_id=current_user.id,
            summary=f"Deleted {label}",
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error deleting client {client_id}: {e}")
        return jsonify(success=False, message="Something went wrong — nothing was deleted."), 500

    delete_photos(photos)
    logging.info(f"Super-admin {current_user.id} deleted client {client_id}")
    return jsonify(success=True, message=f"Deleted {label}.")


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
