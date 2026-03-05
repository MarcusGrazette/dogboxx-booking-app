"""
Client routes.

This module defines routes for client functionality, including home page, profile
management, onboarding, and booking management.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import User, Client, Dog, Booking, DogOwner, ServiceType
from app import db
from app.utils.db_error_handler import handle_db_errors, DBErrorHandler
from app.utils.uploads import process_dog_photo
from app.forms import OnboardingForm, BookingForm, ProfileForm
import logging
import traceback
from datetime import datetime, timezone, timedelta

from app.blueprints.client import client_bp


@client_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """Render the home page for clients."""
    # Check if user is a client
    if current_user.is_admin:
        return redirect(url_for('admin.index'))
    elif current_user.role == 'walker':
        return redirect(url_for('walker.schedule'))
        
    user = User.query.options(
        joinedload(User.client)
    ).filter_by(id=current_user.id).first()
    
    # Get user's dogs through DogOwner relationship
    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()

    # Return upcoming bookings
    today = datetime.now(timezone.utc).date()
    upcoming_bookings_query = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.status != 'cancelled',
        Booking.date > today
    ).order_by(Booking.date.asc()).limit(15)

    upcoming_bookings = list(upcoming_bookings_query)
    for b in upcoming_bookings:
        if b.date:
            b.date_display = b.date.strftime("%d %b")
        else:
            b.date_display = None

    form = BookingForm()
    if form.validate_on_submit():
        booking_date = form.date.data
        booking_slot = form.slot.data

        # Set date bounds, not in the past or more than three months in the future
        today = datetime.now(timezone.utc).date()
        max_date = (datetime.now(timezone.utc) + timedelta(days=90)).date()

        errors = []
        
        # Validate slot against allowed enum values
        if booking_slot not in ("Morning", "Afternoon"):
            errors.append("Invalid booking slot selected.")

         # Ensure the user has at least one dog to book
        if not user or not user_dogs:
            errors.append("No dog found on your account. Please add a dog before booking.")

        if not errors:
            dog_id = user_dogs[0].id  # Use first dog from DogOwner relationship
            # Prevent duplicate booking: same dog + date + slot (only active bookings)
            active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
            existing = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == booking_date,
                Booking.slot == booking_slot,
                Booking.status.in_(active_statuses)
            ).first()
            if existing:
                errors.append("This dog already has a booking for that slot on that date.")

            # Cap at 2 bookings per dog per day (one per slot)
            day_count = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == booking_date,
                Booking.status.in_(active_statuses)
            ).count()
            if day_count >= 2:
                errors.append("This dog already has two bookings on that date (one per slot is the maximum).")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            # Use context manager for error handling
            with DBErrorHandler(
                flash_message=True, 
                custom_error_messages={
                    IntegrityError: "Could not create booking due to a conflict. You might already have a booking at this time.",
                    OperationalError: "Our booking system is temporarily unavailable. Please try again later."
                }
            ):
                # Look up default service type by slug
                default_service = ServiceType.query.filter_by(slug='group-walk', active=True).first()
                if not default_service:
                    flash("No service type available. Please contact support.", "danger")
                    return redirect(url_for("client.index"))
                new_booking = Booking(
                    user_id=user.id,
                    dog_id=dog_id,
                    service_type_id=default_service.id,
                    date=booking_date,
                    slot=booking_slot
                )
                db.session.add(new_booking)
                db.session.commit()
                flash("Success - booking request submitted", "success")
                return redirect(url_for("client.index"))
    
    return render_template("index.html", user=user, client=user.client, dogs=user_dogs, bookings=upcoming_bookings, form=form) # type: ignore


@client_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Display and manage client profile, address, notifications and dog info."""
    if current_user.role != 'client':
        return redirect(url_for('client.index'))

    client = Client.query.filter_by(user_id=current_user.id).first()
    if not client or not client.onboarding_completed:
        return redirect(url_for('client.onboard'))

    # Get primary dog
    dog_owner = DogOwner.query.filter_by(user_id=current_user.id, role='primary').first()
    dog = Dog.query.get(dog_owner.dog_id) if dog_owner else None

    form = ProfileForm()

    if form.validate_on_submit():
        try:
            # Personal info
            current_user.firstname = form.firstname.data.strip()
            current_user.lastname = form.lastname.data.strip()

            # Address
            client.street_address = form.address_line_1.data.strip()
            if form.address_line_2.data:
                client.street_address += '\n' + form.address_line_2.data.strip()
            if form.address_line_3.data:
                client.street_address += '\n' + form.address_line_3.data.strip()
            client.postal_code = form.postcode.data.strip()
            client.pickup_instructions = form.pickup_instructions.data.strip() if form.pickup_instructions.data else None

            # Notifications
            current_user.phone = form.phone.data.strip() if form.phone.data else None
            if form.notify_email.data and form.notify_whatsapp.data:
                current_user.notification_preference = 'both'
            elif form.notify_whatsapp.data:
                current_user.notification_preference = 'whatsapp'
            else:
                current_user.notification_preference = 'email'

            # Dog info
            if dog:
                dog.name = form.dog_name.data.strip()
                dog.gender = form.dog_gender.data.strip()
                dog.breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
                dog.date_of_birth = form.dog_dob.data
                dog.allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""

                # Handle photo upload
                if 'file' in request.files and request.files['file'].filename:
                    try:
                        pic_filename = process_dog_photo(request.files['file'])
                        if pic_filename:
                            dog.pic = pic_filename
                    except ValueError as e:
                        flash(f"Upload error: {str(e)}", "error")
                        return render_template("profile.html", form=form, dog=dog, today=datetime.now().strftime('%Y-%m-%d'))
                    except Exception as e:
                        logging.error(f"Error processing uploaded file: {e}")
                        flash("Error processing your image. Please try a different file.", "error")
                        return render_template("profile.html", form=form, dog=dog, today=datetime.now().strftime('%Y-%m-%d'))

            db.session.commit()
            flash("Profile updated successfully!", "success")
            return redirect(url_for('client.profile'))

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating profile for user {current_user.email}: {e}")
            flash("There was an error saving your changes. Please try again.", "error")

    elif request.method == 'GET':
        # Pre-fill form with existing data
        form.firstname.data = current_user.firstname
        form.lastname.data = current_user.lastname

        # Split street_address back into lines
        if client.street_address:
            address_lines = client.street_address.split('\n')
            form.address_line_1.data = address_lines[0] if len(address_lines) > 0 else ''
            form.address_line_2.data = address_lines[1] if len(address_lines) > 1 else ''
            form.address_line_3.data = address_lines[2] if len(address_lines) > 2 else ''
        form.postcode.data = client.postal_code
        form.pickup_instructions.data = client.pickup_instructions

        # Notifications
        form.phone.data = current_user.phone
        form.notify_email.data = current_user.notification_preference in ('email', 'both')
        form.notify_whatsapp.data = current_user.notification_preference in ('whatsapp', 'both')

        # Dog info
        if dog:
            form.dog_name.data = dog.name
            form.dog_gender.data = dog.gender
            form.dog_breed.data = dog.breed
            form.dog_dob.data = dog.date_of_birth
            form.dog_allergies.data = dog.allergies

    return render_template("profile.html", form=form, dog=dog, today=datetime.now().strftime('%Y-%m-%d'))


@client_bp.route("/onboard", methods=["GET", "POST"])
@login_required
def onboard():
    """Handle complete user onboarding process"""
    if current_user.role != 'client':
        flash("Onboarding is only required for clients.", "info")
        return redirect(url_for('client.index'))

    client = Client.query.filter_by(user_id=current_user.id).first()
    if client and client.onboarding_completed:
        flash("You have already completed onboarding!", "info")
        return redirect(url_for('client.index'))

    form = OnboardingForm()
    if form.validate_on_submit():
        try:
            # Step 1: Address information
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

            pickup_instructions = form.pickup_instructions.data.strip()
            client.pickup_instructions = pickup_instructions
            client.onboarding_completed = True
            client.onboarding_completed_at = datetime.now(timezone.utc)

            # Notification preferences
            current_user.phone = form.phone.data.strip() if form.phone.data else None
            if form.notify_email.data and form.notify_whatsapp.data:
                current_user.notification_preference = 'both'
            elif form.notify_whatsapp.data:
                current_user.notification_preference = 'whatsapp'
            else:
                current_user.notification_preference = 'email'

            # Step 2: Dog information
            dog_name = form.dog_name.data.strip()
            dog_gender = form.dog_gender.data.strip()
            dog_dob = form.dog_dob.data
            dog_breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
            dog_allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""

            # Handle file upload
            pic_filename = None
            if 'file' in request.files:
                try:
                    pic_filename = process_dog_photo(request.files['file'])
                except ValueError as e:
                    logging.error(f"Invalid file upload: {e}")
                    flash(f"Upload error: {str(e)}. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, today=datetime.now().strftime('%Y-%m-%d'))
                except Exception as e:
                    logging.error(f"Error processing uploaded file: {e}")
                    flash("There was an error processing your image. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, today=datetime.now().strftime('%Y-%m-%d'))

            # Create dog record
            new_dog = Dog(
                name=dog_name,
                gender=dog_gender,
                breed=dog_breed,
                allergies=dog_allergies,
                date_of_birth=dog_dob,
                pic=pic_filename
            )
            db.session.add(new_dog)
            db.session.flush()  # Get new_dog.id
            
            # Create DogOwner relationship (user is primary owner)
            dog_owner = DogOwner(
                dog_id=new_dog.id,
                user_id=current_user.id,
                role='primary'
            )
            db.session.add(dog_owner)
            
            # Commit all changes together
            db.session.commit()

            flash(f"Welcome to our platform, {current_user.firstname}! Your profile is now complete.", "success")
            return redirect(url_for('client.index'))

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error during onboarding for user {current_user.email}: {e}")
            logging.debug(f"Exception details: {traceback.format_exc()}")
            
            # Check for specific error types
            if isinstance(e, SQLAlchemyError):
                if isinstance(e, IntegrityError):
                    flash("There was a conflict with existing data. This might be because the information already exists in our system.", "error")
                elif isinstance(e, OperationalError):
                    flash("The database is currently unavailable. Please try again later.", "error")
                else:
                    flash("There was a database error. Please try again.", "error")
            else:
                flash("There was an error saving your information. Please try again.", "error")

    return render_template("onboarding.html", form=form, today=datetime.now().strftime('%Y-%m-%d'))


@client_bp.route("/cancel_booking", methods=["POST"])
@login_required
def cancel_booking():
    """Cancel a booking"""
    if current_user.role != 'client' and not current_user.is_admin:
        return jsonify(success=False, message="Unauthorized"), 403
        
    try:
        booking_id = request.form.get("booking_id") or request.json.get("booking_id")
        if not booking_id:
            return jsonify(success=False, message="No booking ID provided"), 400
            
        booking = Booking.query.get(booking_id)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404
            
        # Check authorization - only allow users to cancel their own bookings or admins to cancel any
        if booking.user_id != current_user.id and not current_user.is_admin:
            return jsonify(success=False, message="You are not authorized to cancel this booking"), 403
            
        booking.status = "cancelled"
        booking.walker_id = None  # Unassign walker
        db.session.commit()
        
        return jsonify(success=True, message="Booking successfully cancelled")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error cancelling booking: {e}")
        return jsonify(success=False, message="Server error"), 500
