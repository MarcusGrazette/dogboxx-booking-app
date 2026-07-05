"""
Client profile routes — profile page, monthly billing summary, photo
uploads, and per-dog pickup/detail edits.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from app.models import User, Client, Dog, Booking, DogOwner
from app import db
from app.utils.uploads import process_dog_photo, process_cropped_photo
from app.utils.booking_access import get_accessible_dog_ids
from app.forms import ProfileForm
import logging
from datetime import datetime, timezone, date as date_type

from app.blueprints.client import client_bp
from app.utils.decorators import has_client_access


@client_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Display and manage client profile, address, notifications and dog info."""
    if not has_client_access(current_user):
        return redirect(url_for('client.index'))

    client = Client.query.filter_by(user_id=current_user.id).first()

    # Get all primary dogs (user is the main owner — can edit photo/details)
    primary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='primary').all()
    primary_dogs = []
    for _po in primary_ownerships:
        _d = db.session.get(Dog, _po.dog_id)
        if _d:
            primary_dogs.append(_d)
    dog = primary_dogs[0] if primary_dogs else None  # first primary dog (for form hidden fields)

    # Secondary-only owners (co-owners with no primary dog of their own) should be
    # allowed to view/edit their profile without going through onboarding.
    is_secondary_only = (not primary_ownerships and
                         DogOwner.query.filter_by(user_id=current_user.id, role='secondary').first() is not None)

    if not is_secondary_only and (not client or not client.onboarding_completed):
        return redirect(url_for('client.onboard'))

    # Get secondary dogs (user has shared access — read-only on the profile)
    secondary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='secondary').all()
    secondary_dogs = []
    for so in secondary_ownerships:
        secondary_dog = db.session.get(Dog, so.dog_id)
        if not secondary_dog:
            continue
        primary_o = DogOwner.query.filter_by(dog_id=so.dog_id, role='primary').first()
        primary_user = db.session.get(User, primary_o.user_id) if primary_o else None
        primary_client = Client.query.filter_by(user_id=primary_o.user_id).first() if primary_o else None
        secondary_dogs.append({'dog': secondary_dog, 'primary_owner': primary_user, 'primary_client': primary_client})

    # Booking stats for the profile sidebar
    # Use dog_ids so secondary owners see all bookings for their shared dog,
    # not just bookings they personally created.
    from datetime import date
    today_date = date.today()
    month_start = date(today_date.year, today_date.month, 1)
    if today_date.month == 12:
        month_end = date(today_date.year + 1, 1, 1)
    else:
        month_end = date(today_date.year, today_date.month + 1, 1)

    accessible_dog_ids = get_accessible_dog_ids(current_user.id)

    month_bookings = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.date >= month_start,
        Booking.date < month_end,
        Booking.status.notin_(['cancelled', 'rejected'])
    ).all()
    confirmed_this_month = sum(1 for b in month_bookings if b.status == 'confirmed')
    pending_this_month = sum(1 for b in month_bookings if b.status in ('requested', 'waitlisted'))

    next_booking = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.date >= today_date,
        Booking.status == 'confirmed'
    ).order_by(Booking.date).first()

    booking_stats = {
        'confirmed_this_month': confirmed_this_month,
        'pending_this_month': pending_this_month,
        'total_this_month': len(month_bookings),
        'next_booking': next_booking,
        'month_name': today_date.strftime('%B'),
    }

    form = ProfileForm()

    if form.validate_on_submit():
        try:
            # Personal info
            current_user.firstname = form.firstname.data.strip()
            current_user.lastname = form.lastname.data.strip()

            # Create a Client record on first save if this is a secondary-only owner
            if not client:
                client = Client(user_id=current_user.id, onboarding_completed=True,
                                onboarding_completed_at=datetime.now(timezone.utc))
                db.session.add(client)

            # Address
            client.street_address = form.address_line_1.data.strip()
            if form.address_line_2.data:
                client.street_address += '\n' + form.address_line_2.data.strip()
            if form.address_line_3.data:
                client.street_address += '\n' + form.address_line_3.data.strip()
            client.postal_code = form.postcode.data.strip()
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None

            # Pickup notes live on the dog, not the client
            if primary_dogs:
                # Per-dog raw fields (named pickup_instructions_{id}) in the template
                for _pd in primary_dogs:
                    _val = request.form.get(f'pickup_instructions_{_pd.id}', '').strip() or None
                    _pd.pickup_instructions = _val
            elif secondary_dogs:
                # Secondary-only path: update first shared dog's instructions via form field
                secondary_dogs[0]['dog'].pickup_instructions = (
                    form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
                )

            # Notifications — email toggle controls newsletter subscription
            current_user.email_marketing = bool(form.notify_email.data)
            current_user.notification_preference = 'email'

            # Dog info — name/gender/breed are admin-managed (round-trip via hidden fields)
            # dob and allergies are client-editable via per-dog raw fields
            if dog:
                dog.name = form.dog_name.data.strip()
                dog.gender = form.dog_gender.data.strip()
                dog.breed = form.dog_breed.data.strip() if form.dog_breed.data else ""

            for _pd in primary_dogs:
                # Handle photo upload
                if 'file' in request.files and request.files['file'].filename:
                    try:
                        pic_filename = process_dog_photo(request.files['file'])
                        if pic_filename:
                            dog.pic = pic_filename
                    except ValueError as e:
                        flash(f"Upload error: {str(e)}", "error")
                        return render_template("profile.html", form=form, dog=dog, primary_dogs=primary_dogs, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))
                    except Exception as e:
                        logging.exception(f"Error processing uploaded file: {e}")
                        flash("Error processing your image. Please try a different file.", "error")
                        return render_template("profile.html", form=form, dog=dog, primary_dogs=primary_dogs, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))

            db.session.commit()
            flash("Profile updated successfully!", "success")
            return redirect(url_for('client.profile'))

        except Exception as e:
            db.session.rollback()
            logging.exception(f"Error updating profile for user {current_user.email}: {e}")
            flash("There was an error saving your changes. Please try again.", "error")

    elif request.method == 'GET':
        # Pre-fill form with existing data
        form.firstname.data = current_user.firstname
        form.lastname.data = current_user.lastname

        # Split street_address back into lines
        if client and client.street_address:
            address_lines = client.street_address.split('\n')
            form.address_line_1.data = address_lines[0] if len(address_lines) > 0 else ''
            form.address_line_2.data = address_lines[1] if len(address_lines) > 1 else ''
            form.address_line_3.data = address_lines[2] if len(address_lines) > 2 else ''
        if client:
            form.postcode.data = client.postal_code
            form.maps_url.data = client.maps_url

        # Pickup notes: primary dogs use per-dog raw fields in template;
        # secondary-only path pre-fills the form field for backward compat
        if not primary_dogs and secondary_dogs:
            form.pickup_instructions.data = secondary_dogs[0]['dog'].pickup_instructions

        # Notifications
        form.notify_email.data = current_user.email_marketing

        # Dog info
        if dog:
            form.dog_name.data = dog.name
            form.dog_gender.data = dog.gender
            form.dog_breed.data = dog.breed
            form.dog_dob.data = dog.date_of_birth
            form.dog_allergies.data = dog.allergies

    return render_template("profile.html", form=form, dog=dog, primary_dogs=primary_dogs, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))


@client_bp.route("/monthly-summary")
@login_required
def monthly_summary():
    """Client-facing monthly summary: bookings and estimated charges for a given month."""
    from app.utils.invoicing import invoice_for_client
    from app.utils.pricing import build_line_items, build_double_slot_discounts
    from app.models import PricingConfig

    if not has_client_access(current_user):
        return redirect(url_for('client.index'))

    today = date_type.today()
    month_str = request.args.get('month', f'{today.year}-{today.month:02d}')
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    # Cap at current month — no peeking ahead
    if (year, month) > (today.year, today.month):
        year, month = today.year, today.month

    month_start = date_type(year, month, 1)
    month_end   = date_type(year + (month // 12), (month % 12) + 1, 1)

    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= month_end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    inv = invoice_for_client(current_user.id, month_start, month_end, all_configs)
    if inv is None:
        inv = {
            'confirmed': [], 'late_cancels': [], 'all_billable': [],
            'total_walks': 0, 'total_drop_ins': 0, 'total_cancels': 0,
            'total_billable': 0, 'doubles': 0, 'subtotal': 0.0,
        }

    late_cancel_ids = {b.id for b in inv['late_cancels']}
    line_items = build_line_items(inv['all_billable'], late_cancel_ids, all_configs)
    discounts = build_double_slot_discounts(inv['all_billable'], all_configs)

    # Month nav
    if month == 1:
        prev_month = f'{year - 1}-12'
    else:
        prev_month = f'{year}-{month - 1:02d}'
    if month == 12:
        next_month = f'{year + 1}-01'
    else:
        next_month = f'{year}-{month + 1:02d}'
    at_current = (year == today.year and month == today.month)

    return render_template(
        'client_monthly_summary.html',
        inv=inv,
        line_items=line_items,
        discounts=discounts,
        month_start=month_start,
        prev_month=prev_month,
        next_month=next_month,
        at_current=at_current,
        today=today,
    )


@client_bp.route("/profile/upload-dog-photo", methods=["POST"])
@login_required
def upload_dog_photo():
    """AJAX endpoint: accept a cropped image blob and save it as the dog's photo.

    Expects a multipart POST with a 'file' field containing the canvas blob
    from Cropper.js. Returns JSON {success, url} or {success, error}.
    """
    dog_id_param = request.args.get('dog_id') or request.form.get('dog_id')
    if dog_id_param:
        try:
            dog_id_param = int(dog_id_param)
        except (TypeError, ValueError):
            return jsonify(success=False, error="Invalid dog ID"), 400
        dog_owner = DogOwner.query.filter_by(
            user_id=current_user.id, dog_id=dog_id_param, role='primary'
        ).first()
    else:
        dog_owner = DogOwner.query.filter_by(user_id=current_user.id, role='primary').first()
    dog = db.session.get(Dog, dog_owner.dog_id) if dog_owner else None
    if not dog:
        return jsonify(success=False, error="Dog profile not found"), 404

    if 'file' not in request.files:
        return jsonify(success=False, error="No file provided"), 400

    try:
        filename = process_cropped_photo(request.files['file'])
        if not filename:
            return jsonify(success=False, error="Empty file"), 400

        dog.pic = filename
        db.session.commit()

        url = url_for('static', filename=f'uploads/dogs/{filename}')
        logging.info(f"Dog photo updated for client {current_user.email}: {filename}")
        return jsonify(success=True, url=url)

    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error saving cropped dog photo for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error saving photo"), 500


@client_bp.route("/profile/upload-profile-photo", methods=["POST"])
@login_required
def upload_profile_photo():
    """AJAX endpoint: accept a cropped image blob and save it as the user's profile photo.

    Returns JSON {success, url} or {success, error}.
    """
    if 'file' not in request.files:
        return jsonify(success=False, error="No file provided"), 400

    try:
        filename = process_cropped_photo(request.files['file'], subfolder='profiles')
        if not filename:
            return jsonify(success=False, error="Empty file"), 400

        current_user.profile_pic = filename
        db.session.commit()

        url = url_for('static', filename=f'uploads/profiles/{filename}')
        logging.info(f"Profile photo updated for user {current_user.email}: {filename}")
        return jsonify(success=True, url=url)

    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error saving profile photo for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error saving photo"), 500


@client_bp.route("/profile/update-pickup", methods=["POST"])
@login_required
def update_pickup():
    """AJAX: save pickup instructions (per dog) and newsletter preference."""
    # has_client_access also lets dual-role walkers (role='walker' with a
    # Client record) through. A bare role == 'client' check rejects them
    # even though they own dogs and use the client view.
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403

    primary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='primary').all()
    primary_dogs = [db.session.get(Dog, po.dog_id) for po in primary_ownerships]
    primary_dogs = [d for d in primary_dogs if d]

    secondary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='secondary').all()

    try:
        if primary_dogs:
            for _pd in primary_dogs:
                _val = request.form.get(f'pickup_instructions_{_pd.id}', '').strip() or None
                _pd.pickup_instructions = _val
        elif secondary_ownerships:
            sec_dog = db.session.get(Dog, secondary_ownerships[0].dog_id)
            if sec_dog:
                sec_dog.pickup_instructions = request.form.get('pickup_instructions', '').strip() or None

        current_user.email_marketing = request.form.get('notify_email') == 'true'
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error updating pickup notes for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error"), 500


@client_bp.route("/profile/dog/<int:dog_id>/update-details", methods=["POST"])
@login_required
def update_dog_details(dog_id):
    """AJAX: save DOB and health notes for a dog the current user owns as primary."""
    ownership = DogOwner.query.filter_by(dog_id=dog_id, user_id=current_user.id, role='primary').first()
    if not ownership:
        return jsonify(success=False, error="Not authorised"), 403

    dog = db.session.get(Dog, dog_id)
    if not dog:
        return jsonify(success=False, error="Dog not found"), 404

    try:
        from datetime import date as _date_type
        dob_str = request.form.get('dob', '').strip()
        dog.date_of_birth = _date_type.fromisoformat(dob_str) if dob_str else None
        dog.allergies = request.form.get('health_notes', '').strip() or None
        db.session.commit()
        return jsonify(success=True)
    except ValueError:
        db.session.rollback()
        return jsonify(success=False, error="Invalid date"), 400
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error updating dog details for dog {dog_id}: {e}")
        return jsonify(success=False, error="Server error"), 500
