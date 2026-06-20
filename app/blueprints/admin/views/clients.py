from collections import defaultdict
from flask import request, render_template, redirect, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
import logging

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, Dog, Client, DogOwner, Notification
from app import db
from app.forms import ClientCreateForm
from app.utils.uploads import process_dog_photo
from werkzeug.security import generate_password_hash
import secrets


@admin_bp.route("/clients")
@login_required
@admin_required
def clients():
    """List all clients (admin only)"""
    clients = (
        User.query
        .options(joinedload(User.client), joinedload(User.walker))
        .filter(User.client != None)  # noqa: E711 — SQLAlchemy uses != None for EXISTS check
        .order_by(User.active.desc(), User.lastname, User.firstname)
        .all()
    )

    return render_template("admin_clients.html", clients=clients)


@admin_bp.route("/clients/<int:client_id>")
@login_required
@admin_required
def client_detail(client_id):
    """Show client detail with dog info, shared access, and notification audit trail (admin only)"""
    user = User.query.filter(User.client != None, User.id == client_id).first_or_404()

    # Batch all dog + co-owner lookups to avoid N+1 queries
    primary_ownerships = DogOwner.query.filter_by(user_id=user.id, role='primary').all()
    sec_as_secondary = DogOwner.query.filter_by(user_id=user.id, role='secondary').all()

    all_dog_ids = [o.dog_id for o in primary_ownerships + sec_as_secondary]
    dogs_by_id = (
        {d.id: d for d in Dog.query.filter(Dog.id.in_(all_dog_ids)).all()}
        if all_dog_ids else {}
    )

    all_co_ownerships = (
        DogOwner.query.filter(DogOwner.dog_id.in_(all_dog_ids)).all()
        if all_dog_ids else []
    )
    co_user_ids = [o.user_id for o in all_co_ownerships if o.user_id != user.id]
    co_users_by_id = (
        {u.id: u for u in User.query.filter(User.id.in_(co_user_ids)).all()}
        if co_user_ids else {}
    )

    # Index co-ownerships by dog_id + role for fast lookup
    secondary_by_dog: dict = defaultdict(list)
    primary_by_dog: dict = {}
    for o in all_co_ownerships:
        if o.role == 'secondary' and o.user_id != user.id:
            u_obj = co_users_by_id.get(o.user_id)
            if u_obj:
                secondary_by_dog[o.dog_id].append(u_obj)
        elif o.role == 'primary' and o.user_id != user.id:
            primary_by_dog[o.dog_id] = co_users_by_id.get(o.user_id)

    # Dogs where this user is the primary owner
    primary_dogs = []
    for ownership in primary_ownerships:
        dog = dogs_by_id.get(ownership.dog_id)
        if not dog:
            continue
        primary_dogs.append({'dog': dog, 'secondary_owners': secondary_by_dog.get(dog.id, [])})

    # Dogs where this user is a secondary owner (joined from another account)
    secondary_dogs = []
    for ownership in sec_as_secondary:
        dog = dogs_by_id.get(ownership.dog_id)
        if not dog:
            continue
        secondary_dogs.append({'dog': dog, 'primary_owner': primary_by_dog.get(dog.id)})

    # Clients available to join — exclude self and anyone already linked
    already_linked_ids = {user.id}
    for pd in primary_dogs:
        for so in pd['secondary_owners']:
            already_linked_ids.add(so.id)
    for sd in secondary_dogs:
        if sd['primary_owner']:
            already_linked_ids.add(sd['primary_owner'].id)
    available_clients = (
        User.query
        .filter(User.role == 'client', User.active == True)
        .filter(~User.id.in_(already_linked_ids))
        .order_by(User.lastname, User.firstname)
        .all()
    )

    # Backward-compat: keep `dog` pointing at first primary dog for old template sections
    dog = primary_dogs[0]['dog'] if primary_dogs else None

    notifications = (
        Notification.query
        .filter_by(recipient_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )
    from app.forms import AddDogForm
    from datetime import date as date_type
    return render_template(
        "admin_client_detail.html",
        client=user,
        dog=dog,
        primary_dogs=primary_dogs,
        secondary_dogs=secondary_dogs,
        available_clients=available_clients,
        notifications=notifications,
        add_dog_form=AddDogForm(),
        add_dog_modal_open=False,
        today=date_type.today(),
    )


@admin_bp.route("/clients/<int:client_id>/join", methods=["POST"])
@login_required
@admin_required
def join_dog_access(client_id):
    """Grant a secondary client shared access to the primary client's dog.

    Expects JSON: { "dog_id": int, "secondary_user_id": int }
    The secondary user gains read/book/cancel access to the dog but is not
    the primary owner — they cannot modify the dog's profile.
    """
    primary_user = User.query.filter(User.client != None, User.id == client_id).first_or_404()
    data = request.get_json(silent=True) or {}
    dog_id = data.get('dog_id')
    secondary_user_id = data.get('secondary_user_id')

    if not dog_id or not secondary_user_id:
        return jsonify(success=False, message="Missing dog_id or secondary_user_id"), 400

    # Verify dog belongs to this primary client
    ownership = DogOwner.query.filter_by(dog_id=dog_id, user_id=primary_user.id, role='primary').first()
    if not ownership:
        return jsonify(success=False, message="Dog not found for this client"), 404

    secondary_user = User.query.filter(User.role == 'client', User.id == secondary_user_id).first()
    if not secondary_user:
        return jsonify(success=False, message="Secondary client not found"), 404

    if secondary_user_id == client_id:
        return jsonify(success=False, message="Cannot join an account to itself"), 400

    existing = DogOwner.query.filter_by(dog_id=dog_id, user_id=secondary_user_id).first()
    if existing:
        return jsonify(success=False, message=f"{secondary_user.full_name} already has access to this dog"), 409

    try:
        db.session.add(DogOwner(dog_id=dog_id, user_id=secondary_user_id, role='secondary'))

        # If the secondary user hasn't completed onboarding yet (e.g. admin created
        # their account without a dog), mark it complete — they'll use the shared dog
        # and don't need to go through the onboarding flow.
        secondary_client = Client.query.filter_by(user_id=secondary_user_id).first()
        if secondary_client and not secondary_client.onboarding_completed:
            secondary_client.onboarding_completed = True
            secondary_client.onboarding_completed_at = datetime.now(timezone.utc)

        db.session.commit()
        logging.info(
            f"Admin {current_user.id} granted {secondary_user.email} secondary access "
            f"to dog {dog_id} (primary: {primary_user.email})"
        )
        return jsonify(
            success=True,
            message=f"{secondary_user.full_name} now has access to {ownership.dog.name}",
            secondary_user={'id': secondary_user.id, 'full_name': secondary_user.full_name, 'email': secondary_user.email},
        )
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error joining accounts: {e}")
        return jsonify(success=False, message="An error occurred"), 500


@admin_bp.route("/clients/<int:client_id>/revoke-access", methods=["POST"])
@login_required
@admin_required
def revoke_dog_access(client_id):
    """Remove a secondary client's shared access to a dog.

    Expects JSON: { "dog_id": int, "secondary_user_id": int }
    Can be called from either the primary or secondary client's detail page.
    Will not remove primary ownership.
    """
    User.query.filter(User.client != None, User.id == client_id).first_or_404()
    data = request.get_json(silent=True) or {}
    dog_id = data.get('dog_id')
    secondary_user_id = data.get('secondary_user_id')

    if not dog_id or not secondary_user_id:
        return jsonify(success=False, message="Missing dog_id or secondary_user_id"), 400

    record = DogOwner.query.filter_by(dog_id=dog_id, user_id=secondary_user_id, role='secondary').first()
    if not record:
        return jsonify(success=False, message="No secondary access record found"), 404

    secondary_user = db.session.get(User, secondary_user_id)
    dog = db.session.get(Dog, dog_id)

    try:
        db.session.delete(record)
        db.session.commit()
        logging.info(
            f"Admin {current_user.id} revoked secondary access for user {secondary_user_id} "
            f"from dog {dog_id}"
        )
        # Notify the secondary user their access was removed
        if secondary_user and dog:
            from app.utils.notifications import create_notification
            create_notification(
                recipient_id=secondary_user.id,
                notification_type='system',
                title=f"Your access to {dog.name} has been removed",
                body="Contact Dogboxx if you think this is a mistake.",
                link='/profile',
            )
            db.session.commit()
        return jsonify(
            success=True,
            message=f"Access revoked for {secondary_user.full_name if secondary_user else secondary_user_id}",
        )
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error revoking dog access: {e}")
        return jsonify(success=False, message="An error occurred"), 500


@admin_bp.route("/clients/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_client():
    """Create a new client account with full details (admin only).

    The admin can fill in address, pickup notes, and dog info upfront so that
    the client sees their profile (and any pre-created bookings) the moment
    they first log in.  Onboarding is marked complete automatically when both
    address and dog info are provided; otherwise the client will still be
    prompted to complete the remaining steps on first login.
    """
    form = ClientCreateForm()

    if form.validate_on_submit():
        try:
            existing_user = User.query.filter_by(email=form.email.data.lower()).first()
            if existing_user:
                flash("A user with this email already exists.", "error")
                return render_template("admin_client_form.html", form=form, title="Add New Client", is_edit=False)

            temp_password = secrets.token_urlsafe(12)

            user = User(
                firstname=form.firstname.data.strip().title(),
                lastname=form.lastname.data.strip().title(),
                email=form.email.data.strip().lower(),
                role='client',
                hashed_password=generate_password_hash(temp_password),
                must_change_password=True,
            )
            user.notification_preference = 'email'
            user.email_marketing = bool(form.notify_email.data)
            user.phone = form.phone.data.strip() if form.phone.data else None

            db.session.add(user)
            db.session.flush()  # get user.id

            # Build Client record
            client = Client(user_id=user.id)
            has_address = bool(form.address_line_1.data and form.address_line_1.data.strip())
            if has_address:
                client.street_address = form.address_line_1.data.strip()
                if form.address_line_2.data and form.address_line_2.data.strip():
                    client.street_address += '\n' + form.address_line_2.data.strip()
                if form.address_line_3.data and form.address_line_3.data.strip():
                    client.street_address += '\n' + form.address_line_3.data.strip()
                client.postal_code = form.postcode.data.strip() if form.postcode.data else None
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None
            db.session.add(client)
            db.session.flush()  # get client.id

            # Create Dog record if core dog fields are present
            has_dog = bool(form.dog_name.data and form.dog_name.data.strip() and form.dog_gender.data)
            if has_dog:
                new_dog = Dog(
                    name=form.dog_name.data.strip(),
                    gender=form.dog_gender.data,
                    breed=form.dog_breed.data.strip() if form.dog_breed.data else "",
                    allergies=form.dog_allergies.data.strip() if form.dog_allergies.data else "",
                    date_of_birth=form.dog_dob.data,
                    whatsapp_group_url=(form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None,
                    pickup_instructions=form.pickup_instructions.data.strip() if form.pickup_instructions.data else None,
                    hold_key=bool(form.hold_key.data),
                )
                db.session.add(new_dog)
                db.session.flush()
                db.session.add(DogOwner(dog_id=new_dog.id, user_id=user.id, role='primary'))

            # Mark onboarding complete when the admin has provided everything
            if has_address and has_dog:
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)

            db.session.commit()

            logging.info(f"Admin {current_user.id} created client account for {user.email} "
                         f"(address={'yes' if has_address else 'no'}, dog={'yes' if has_dog else 'no'})")

            flash(f"Client account created for {user.firstname} {user.lastname}.", "success")
            return redirect(url_for('admin.client_detail', client_id=user.id))

        except IntegrityError as e:
            db.session.rollback()
            logging.error(f"IntegrityError creating client: {e}")
            flash("A client with this email already exists.", "error")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating client: {e}")
            flash("An error occurred while creating the client.", "error")

    return render_template("admin_client_form.html", form=form, title="Add New Client", is_edit=False)


@admin_bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_client(client_id):
    """Edit an existing client's details (admin only).

    Updates name, address, pickup notes, notification preferences, and dog
    info.  Will create a dog record if one doesn't exist yet.  Marks
    onboarding complete automatically when address + dog are both present.
    """
    user = User.query.filter(User.client != None, User.id == client_id).first_or_404()
    client = Client.query.filter_by(user_id=user.id).first()
    dog_owners = DogOwner.query.filter_by(user_id=user.id, role='primary').order_by(DogOwner.id).all()
    dog = db.session.get(Dog, dog_owners[0].dog_id) if dog_owners else None
    additional_dogs = [db.session.get(Dog, do.dog_id) for do in dog_owners[1:]] if len(dog_owners) > 1 else []

    form = ClientCreateForm()

    if form.validate_on_submit():
        try:
            # Normalise and apply an email change if any. Lowercased to match
            # the login flow (auth/routes.py:35); a unique constraint on
            # User.email guards against collisions — the IntegrityError handler
            # below converts that to a friendly form error.
            submitted_email = form.email.data.strip().lower() if form.email.data else ''
            old_email = user.email
            if submitted_email and submitted_email != old_email:
                user.email = submitted_email
                logging.info(
                    f"Admin {current_user.id} changed email for user {user.id}: "
                    f"{old_email} → {submitted_email}"
                )

            user.firstname = form.firstname.data.strip().title()
            user.lastname = form.lastname.data.strip().title()

            user.notification_preference = 'email'
            user.email_marketing = bool(form.notify_email.data)
            user.phone = form.phone.data.strip() if form.phone.data else None

            if not client:
                client = Client(user_id=user.id)
                db.session.add(client)

            has_address = bool(form.address_line_1.data and form.address_line_1.data.strip())
            if has_address:
                client.street_address = form.address_line_1.data.strip()
                if form.address_line_2.data and form.address_line_2.data.strip():
                    client.street_address += '\n' + form.address_line_2.data.strip()
                if form.address_line_3.data and form.address_line_3.data.strip():
                    client.street_address += '\n' + form.address_line_3.data.strip()
                client.postal_code = form.postcode.data.strip() if form.postcode.data else None
            else:
                client.street_address = None
                client.postal_code = None
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None

            has_dog = bool(form.dog_name.data and form.dog_name.data.strip() and form.dog_gender.data)
            pickup_notes = form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
            if has_dog:
                if dog:
                    dog.name = form.dog_name.data.strip()
                    dog.gender = form.dog_gender.data
                    dog.breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
                    dog.allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""
                    dog.date_of_birth = form.dog_dob.data
                    dog.whatsapp_group_url = (form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None
                    dog.pickup_instructions = pickup_notes
                    dog.hold_key = bool(form.hold_key.data)
                else:
                    new_dog = Dog(
                        name=form.dog_name.data.strip(),
                        gender=form.dog_gender.data,
                        breed=form.dog_breed.data.strip() if form.dog_breed.data else "",
                        allergies=form.dog_allergies.data.strip() if form.dog_allergies.data else "",
                        date_of_birth=form.dog_dob.data,
                        whatsapp_group_url=(form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None,
                        pickup_instructions=pickup_notes,
                        hold_key=bool(form.hold_key.data),
                    )
                    db.session.add(new_dog)
                    db.session.flush()
                    db.session.add(DogOwner(dog_id=new_dog.id, user_id=user.id, role='primary'))

            # Additional dogs (rendered with raw name="dog_<field>_<id>" inputs)
            import re as _re
            for key in list(request.form.keys()):
                m = _re.match(r'^dog_name_(\d+)$', key)
                if not m:
                    continue
                did = int(m.group(1))
                extra_dog = db.session.get(Dog, did)
                if not extra_dog:
                    continue
                extra_name = request.form.get(f'dog_name_{did}', '').strip()
                if extra_name:
                    extra_dog.name = extra_name
                extra_dog.breed = request.form.get(f'dog_breed_{did}', '').strip()
                extra_dog.gender = request.form.get(f'dog_gender_{did}', extra_dog.gender) or extra_dog.gender
                dob_str = request.form.get(f'dog_dob_{did}', '').strip()
                if dob_str:
                    try:
                        extra_dog.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date()
                    except ValueError:
                        pass
                else:
                    extra_dog.date_of_birth = None
                extra_dog.allergies = request.form.get(f'dog_allergies_{did}', '').strip()
                extra_dog.pickup_instructions = request.form.get(f'dog_pickup_instructions_{did}', '').strip() or None
                extra_dog.whatsapp_group_url = request.form.get(f'dog_whatsapp_{did}', '').strip() or None
                extra_dog.hold_key = bool(request.form.get(f'dog_hold_key_{did}'))

            # Auto-complete onboarding when we now have the full picture
            if has_address and has_dog and not client.onboarding_completed:
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)

            db.session.commit()
            flash("Client details updated successfully.", "success")
            return redirect(url_for('admin.client_detail', client_id=user.id))

        except IntegrityError as e:
            db.session.rollback()
            # Almost always the unique constraint on User.email — surface it as
            # a field-level error rather than a generic "something went wrong".
            logging.warning(f"IntegrityError editing client {client_id}: {e}")
            form.email.errors.append(
                "Another account already uses this email."
            )
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error editing client {client_id}: {e}")
            flash("An error occurred while saving changes.", "error")

    elif request.method == 'GET':
        form.firstname.data = user.firstname
        form.lastname.data = user.lastname
        form.email.data = user.email
        form.phone.data = user.phone
        form.notify_email.data = user.email_marketing

        if client:
            if client.street_address:
                lines = client.street_address.split('\n')
                form.address_line_1.data = lines[0] if len(lines) > 0 else ''
                form.address_line_2.data = lines[1] if len(lines) > 1 else ''
                form.address_line_3.data = lines[2] if len(lines) > 2 else ''
            form.postcode.data = client.postal_code
            form.maps_url.data = client.maps_url

        if dog:
            form.pickup_instructions.data = dog.pickup_instructions

        if dog:
            form.dog_name.data = dog.name
            form.dog_gender.data = dog.gender
            form.dog_breed.data = dog.breed
            form.dog_dob.data = dog.date_of_birth
            form.dog_allergies.data = dog.allergies
            form.dog_whatsapp_group_url.data = dog.whatsapp_group_url
            form.hold_key.data = dog.hold_key

    return render_template(
        "admin_client_form.html",
        form=form,
        title=f"Edit {user.full_name}",
        is_edit=True,
        client_user=user,
        additional_dogs=additional_dogs,
    )


@admin_bp.route("/clients/<int:client_id>/add-dog", methods=["POST"])
@login_required
@admin_required
def add_dog(client_id):
    """Add a second (or subsequent) primary dog to an existing client."""
    from app.forms import AddDogForm
    user = User.query.filter(User.client != None, User.id == client_id).first_or_404()

    form = AddDogForm()
    if form.validate_on_submit():
        try:
            new_dog = Dog(
                name=form.dog_name.data.strip(),
                gender=form.dog_gender.data,
                breed=form.dog_breed.data.strip() if form.dog_breed.data else "",
                date_of_birth=form.dog_dob.data,
                allergies=form.dog_allergies.data.strip() if form.dog_allergies.data else "",
                pickup_instructions=form.pickup_instructions.data.strip() if form.pickup_instructions.data else None,
                whatsapp_group_url=(form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None,
                hold_key=bool(form.hold_key.data),
            )
            db.session.add(new_dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=new_dog.id, user_id=user.id, role='primary'))
            db.session.commit()
            flash(f"{new_dog.name} added successfully.", "success")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error adding dog for client {client_id}: {e}")
            flash("An error occurred while adding the dog.", "error")
        return redirect(url_for('admin.client_detail', client_id=client_id))

    # Validation failed — re-render the detail page with the modal open
    # Re-build everything client_detail needs
    primary_ownerships = DogOwner.query.filter_by(user_id=user.id, role='primary').all()
    primary_dogs = []
    for ownership in primary_ownerships:
        dog = db.session.get(Dog, ownership.dog_id)
        if not dog:
            continue
        secondary_ownerships = DogOwner.query.filter_by(dog_id=dog.id, role='secondary').all()
        secondary_users = [db.session.get(User, so.user_id) for so in secondary_ownerships]
        secondary_users = [u for u in secondary_users if u]
        primary_dogs.append({'dog': dog, 'secondary_owners': secondary_users})

    secondary_ownerships = DogOwner.query.filter_by(user_id=user.id, role='secondary').all()
    secondary_dogs = []
    for ownership in secondary_ownerships:
        dog = db.session.get(Dog, ownership.dog_id)
        if not dog:
            continue
        primary_o = DogOwner.query.filter_by(dog_id=dog.id, role='primary').first()
        primary_user = db.session.get(User, primary_o.user_id) if primary_o else None
        secondary_dogs.append({'dog': dog, 'primary_owner': primary_user})

    already_linked_ids = {user.id}
    for pd in primary_dogs:
        for so in pd['secondary_owners']:
            already_linked_ids.add(so.id)
    for sd in secondary_dogs:
        if sd['primary_owner']:
            already_linked_ids.add(sd['primary_owner'].id)
    available_clients = (
        User.query
        .filter(User.role == 'client', User.active == True)
        .filter(~User.id.in_(already_linked_ids))
        .order_by(User.lastname, User.firstname)
        .all()
    )

    dog = primary_dogs[0]['dog'] if primary_dogs else None
    notifications = (
        Notification.query
        .filter_by(recipient_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "admin_client_detail.html",
        client=user,
        dog=dog,
        primary_dogs=primary_dogs,
        secondary_dogs=secondary_dogs,
        available_clients=available_clients,
        notifications=notifications,
        add_dog_form=form,
        add_dog_modal_open=True,
    )


@admin_bp.route("/clients/<int:client_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_client(client_id):
    """Deactivate a client (soft delete)"""
    try:
        user = User.query.filter(User.client != None, User.id == client_id).first()
        if not user:
            return jsonify(success=False, message="Client not found"), 404

        if user.id == current_user.id:
            return jsonify(success=False, message="You cannot deactivate your own account"), 400

        user.active = False
        db.session.commit()

        logging.info(f"Admin {current_user.id} deactivated client {user.id}")
        return jsonify(success=True, message="Client deactivated successfully")

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deactivating client {client_id}: {e}")
        return jsonify(success=False, message="Error deactivating client"), 500


@admin_bp.route("/clients/<int:client_id>/activate", methods=["POST"])
@login_required
@admin_required
def activate_client(client_id):
    """Reactivate a client"""
    try:
        user = User.query.filter(User.client != None, User.id == client_id).first()
        if not user:
            return jsonify(success=False, message="Client not found"), 404

        user.active = True
        db.session.commit()

        logging.info(f"Admin {current_user.id} activated client {user.id}")
        return jsonify(success=True, message="Client activated successfully")

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error activating client {client_id}: {e}")
        return jsonify(success=False, message="Error activating client"), 500


@admin_bp.route("/clients/<int:client_id>/pickup-details", methods=["POST"])
@login_required
@admin_required
def update_client_pickup_details(client_id):
    """Save pickup_instructions and maps_url for a client (admin only)."""
    user = User.query.filter(User.client != None, User.id == client_id).first()
    if not user:
        return jsonify(success=False, message="Client not found"), 404

    client = Client.query.filter_by(user_id=user.id).first()
    if not client:
        return jsonify(success=False, message="Client record not found"), 404

    data = request.get_json(silent=True) or {}
    pickup_instructions = (data.get('pickup_instructions') or '').strip() or None

    if pickup_instructions and len(pickup_instructions) > 1000:
        return jsonify(success=False, message="Instructions too long (max 1000 chars)"), 400

    if 'maps_url' in data:
        maps_url = (data.get('maps_url') or '').strip() or None
        if maps_url and len(maps_url) > 2048:
            return jsonify(success=False, message="Maps URL too long"), 400
        client.maps_url = maps_url

    # Pickup notes now live on the dog, not the client
    from app.models import DogOwner
    dog_owner = DogOwner.query.filter_by(user_id=user.id, role='primary').first()
    dog = db.session.get(Dog, dog_owner.dog_id) if dog_owner else None
    if not dog:
        return jsonify(success=False, message="No dog record found — add a dog first before saving pickup notes"), 404
    dog.pickup_instructions = pickup_instructions
    db.session.commit()
    return jsonify(success=True)
