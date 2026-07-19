"""
Client onboarding routes — account-pending holding page and the onboarding
form (address + dog info completion after admin account setup).
"""

from flask import request, redirect, render_template, flash, url_for
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import Client, Dog, DogOwner
from app import db
from app.utils.uploads import process_dog_photo
from app.utils.sanitize import clean_rich_text_or_none
from app.forms import OnboardingForm
import logging
import traceback
from datetime import datetime, timezone

from app.blueprints.client import client_bp


@client_bp.route("/account-pending")
@login_required
def account_pending():
    """Holding page for client users whose account exists but has no Client record yet.

    This happens when an admin creates a User login but hasn't filled in the
    client details (address / dog info) in the admin panel.  The before_request
    guard redirects them here instead of to /onboard, which requires a Client row.
    """
    from app.models import Client
    # If the Client record appears (admin just finished setting up), redirect onward.
    client = Client.query.filter_by(user_id=current_user.id).first()
    if client:
        if client.onboarding_completed:
            return redirect(url_for('client.index'))
        return redirect(url_for('client.onboard'))
    return render_template('account_pending.html')


@client_bp.route("/onboard", methods=["GET", "POST"])
@login_required
def onboard():
    """Handle client onboarding.

    If the admin has already filled in address + dog info, onboarding_completed
    will already be True and this route redirects away immediately.  Otherwise
    the client fills in whatever the admin left blank.

    If the admin created a dog record during account setup, we update that
    existing dog rather than creating a duplicate.
    """
    if current_user.role != 'client':
        flash("Onboarding is only required for clients.", "info")
        return redirect(url_for('client.index'))

    client = Client.query.filter_by(user_id=current_user.id).first()
    if client and client.onboarding_completed:
        return redirect(url_for('client.index'))

    # Check for a dog already created by the admin
    existing_dog_owner = DogOwner.query.filter_by(user_id=current_user.id, role='primary').first()
    existing_dog = db.session.get(Dog, existing_dog_owner.dog_id) if existing_dog_owner else None

    has_address = bool(client and client.street_address)
    has_dog_info = bool(existing_dog and existing_dog.name and existing_dog.gender)

    form = OnboardingForm()

    if form.validate_on_submit():
        try:
            if not client:
                client = Client(user_id=current_user.id)
                db.session.add(client)

            # Address
            client.street_address = form.address_line_1.data.strip()
            if form.address_line_2.data:
                client.street_address += '\n' + form.address_line_2.data.strip()
            if form.address_line_3.data:
                client.street_address += '\n' + form.address_line_3.data.strip()
            client.postal_code = form.postcode.data.strip()
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None
            client.onboarding_completed = True
            client.onboarding_completed_at = datetime.now(timezone.utc)

            current_user.notification_preference = 'email'

            # Handle file upload
            pic_filename = None
            if 'file' in request.files:
                try:
                    pic_filename = process_dog_photo(request.files['file'])
                except ValueError as e:
                    logging.error(f"Invalid file upload: {e}")
                    flash(f"Upload error: {str(e)}. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, existing_dog=existing_dog, has_address=has_address, has_dog_info=has_dog_info, today=datetime.now().strftime('%Y-%m-%d'))
                except Exception as e:
                    logging.exception(f"Error processing uploaded file: {e}")
                    flash("There was an error processing your image. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, existing_dog=existing_dog, has_address=has_address, has_dog_info=has_dog_info, today=datetime.now().strftime('%Y-%m-%d'))

            # Dog: update existing record if admin already created one, else create fresh
            dog_name = form.dog_name.data.strip()
            dog_gender = form.dog_gender.data.strip()
            dog_dob = form.dog_dob.data
            dog_breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
            dog_allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""

            pickup_notes = clean_rich_text_or_none(form.pickup_instructions.data)
            if existing_dog:
                existing_dog.name = dog_name
                existing_dog.gender = dog_gender
                existing_dog.breed = dog_breed
                existing_dog.allergies = dog_allergies
                existing_dog.date_of_birth = dog_dob
                existing_dog.pickup_instructions = pickup_notes
                if pic_filename:
                    existing_dog.pic = pic_filename
            else:
                new_dog = Dog(
                    name=dog_name,
                    gender=dog_gender,
                    breed=dog_breed,
                    allergies=dog_allergies,
                    date_of_birth=dog_dob,
                    pic=pic_filename,
                    pickup_instructions=pickup_notes,
                )
                db.session.add(new_dog)
                db.session.flush()
                db.session.add(DogOwner(dog_id=new_dog.id, user_id=current_user.id, role='primary'))

            db.session.commit()

            flash(f"Welcome to our platform, {current_user.firstname}! Your profile is now complete.", "success")
            return redirect(url_for('client.index'))

        except Exception as e:
            db.session.rollback()
            logging.exception(f"Error during onboarding for user {current_user.email}: {e}")
            logging.debug(f"Exception details: {traceback.format_exc()}")

            if isinstance(e, SQLAlchemyError):
                if isinstance(e, IntegrityError):
                    flash("There was a conflict with existing data. This might be because the information already exists in our system.", "error")
                elif isinstance(e, OperationalError):
                    flash("The database is currently unavailable. Please try again later.", "error")
                else:
                    flash("There was a database error. Please try again.", "error")
            else:
                flash("There was an error saving your information. Please try again.", "error")

    elif request.method == 'GET':
        # Pre-fill anything the admin already entered — address and/or dog
        if client:
            if client.street_address:
                lines = client.street_address.split('\n')
                form.address_line_1.data = lines[0] if len(lines) > 0 else ''
                form.address_line_2.data = lines[1] if len(lines) > 1 else ''
                form.address_line_3.data = lines[2] if len(lines) > 2 else ''
            form.postcode.data = client.postal_code
            form.pickup_instructions.data = existing_dog.pickup_instructions if existing_dog else None
            form.maps_url.data = client.maps_url
        form.notify_email.data = current_user.email_marketing
        if existing_dog:
            form.dog_name.data = existing_dog.name
            form.dog_gender.data = existing_dog.gender
            form.dog_breed.data = existing_dog.breed
            form.dog_dob.data = existing_dog.date_of_birth
            form.dog_allergies.data = existing_dog.allergies

    return render_template("onboarding.html", form=form, existing_dog=existing_dog, has_address=has_address, has_dog_info=has_dog_info, today=datetime.now().strftime('%Y-%m-%d'))
