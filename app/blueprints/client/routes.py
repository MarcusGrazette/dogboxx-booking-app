"""
Client routes.

This module defines routes for client functionality, including home page, profile
management, onboarding, and booking management.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import User, Client, Dog, Booking
from app import db
from app.utils.db_error_handler import handle_db_errors, DBErrorHandler
from app.forms import OnboardingForm, BookingForm
import logging
import os
import traceback
from datetime import datetime, timezone, timedelta
from PIL import Image
from pathlib import Path
import uuid

from app.blueprints.client import client_bp


@client_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """Render the home page for clients."""
    # Check if user is a client
    if current_user.role != 'client':
        return redirect(url_for(f'{current_user.role}.index'))
        
    user = User.query.options(
    joinedload(User.client),  
    joinedload(User.dogs)
    ).filter_by(id=current_user.id).first()

    # Return upcoming bookings
    today = datetime.now(timezone.utc).date()
    upcoming_bookings_query = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.status != 'Cancelled',
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
        if not user or not getattr(user, "dogs", None):
            errors.append("No dog found on your account. Please add a dog before booking.")

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
                dog_id = user.dogs[0].id  # This assumes a one to one user to dog relationship
                new_booking = Booking(
                    user_id=user.id,
                    dog_id=dog_id,
                    date=booking_date,
                    slot=booking_slot
                )
                db.session.add(new_booking)
                db.session.commit()
                flash("Success - booking request submitted", "success")
                return redirect(url_for("client.index"))
    
    return render_template("index.html", user=user, client=user.client, dogs=user.dogs, bookings=upcoming_bookings, form=form) # type: ignore


@client_bp.route("/profile")
@login_required
def profile():
    """Display and manage user profile"""
    if current_user.role != 'client':
        return redirect(url_for(f'{current_user.role}.profile'))
        
    # Add client profile functionality here
    return "Client Profile Page - Coming Soon"


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
            place_id = form.place_id.data.strip() # type: ignore
            formatted_address = form.formatted_address.data.strip() # type: ignore
            display_name = form.display_name.data.strip()
            latitude = float(form.latitude.data) if form.latitude.data else None
            longitude = float(form.longitude.data) if form.longitude.data else None
            pickup_instructions = form.pickup_instructions.data.strip()

            # Create or update client record
            if not client:
                client = Client(user_id=current_user.id)
                db.session.add(client)

            client.place_id = place_id
            client.formatted_address = formatted_address
            client.display_name = display_name
            client.latitude = latitude
            client.longitude = longitude
            client.pickup_instructions = pickup_instructions
            client.onboarding_completed = True
            client.onboarding_completed_at = datetime.now(timezone.utc)

            # Step 2: Dog information
            dog_name = form.dog_name.data.strip()
            dog_gender = form.dog_gender.data.strip()
            dog_years = int(form.dog_years.data)
            dog_months = int(form.dog_months.data)
            dog_breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
            dog_allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""
        

            # Calculate birth year and month
            today = datetime.now()
            birth_year = today.year - dog_years
            birth_month = today.month - dog_months
            if birth_month <= 0:
                birth_year -= 1
                birth_month += 12

            # Handle file upload with enhanced security
            pic_filename = None
            if 'file' in request.files:
                dog_pic = request.files['file']
                if dog_pic and dog_pic.filename:
                    try:
                        # 1. Verify it's a valid image by attempting to open it
                        try:
                            img = Image.open(dog_pic)
                            img.verify()  # Verify it's a valid image
                            dog_pic.seek(0)  # Reset file pointer after verification
                        except Exception:
                            raise ValueError("Invalid image file")
                        
                        # 2. Check allowed extensions using a whitelist approach
                        file_extension = Path(secure_filename(dog_pic.filename)).suffix.lower()
                        if file_extension not in ['.jpg', '.jpeg', '.png', '.gif']:
                            raise ValueError("Unsupported file format")
                            
                        # 3. Process image to strip metadata and resize
                        img = Image.open(dog_pic)
                        
                        # Create a new image without metadata
                        img_without_exif = Image.new(img.mode, img.size)
                        img_without_exif.putdata(list(img.getdata()))
                        
                        # Resize the image to a standard size
                        max_size = (800, 800)
                        img_without_exif.thumbnail(max_size, Image.LANCZOS)
                        
                        # 4. Generate completely new filename
                        unique_filename = f"{uuid.uuid4()}{file_extension}"
                        from flask import current_app
                        upload_path = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_filename)
                        
                        # Save with appropriate format
                        if file_extension == '.png':
                            img_without_exif.save(upload_path, 'PNG')
                        elif file_extension in ['.jpg', '.jpeg']:
                            img_without_exif.save(upload_path, 'JPEG', quality=85)
                        elif file_extension == '.gif':
                            img_without_exif.save(upload_path, 'GIF')
                        
                        pic_filename = unique_filename
                        
                    except ValueError as e:
                        logging.error(f"Invalid file upload: {e}")
                        flash(f"Upload error: {str(e)}. Please try a different file.", "error")
                        return render_template("onboarding.html", google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'), form=form)
                    except Exception as e:
                        logging.error(f"Error processing uploaded file: {e}")
                        flash("There was an error processing your image. Please try a different file.", "error")
                        return render_template("onboarding.html", google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'), form=form)

            # Create dog record
            new_dog = Dog(
                user_id=current_user.id,
                name=dog_name,
                gender=dog_gender,
                breed=dog_breed,
                allergies=dog_allergies,
                birth_year_month=birth_year * 100 + birth_month,
                pic=pic_filename
            )
            db.session.add(new_dog)
            
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

    return render_template("onboarding.html", google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'), form=form)


@client_bp.route("/cancel_booking", methods=["POST"])
@login_required
def cancel_booking():
    """Cancel a booking"""
    if current_user.role != 'client' and current_user.role != 'admin':
        return jsonify(success=False, message="Unauthorized"), 403
        
    try:
        booking_id = request.form.get("booking_id") or request.json.get("booking_id")
        if not booking_id:
            return jsonify(success=False, message="No booking ID provided"), 400
            
        booking = Booking.query.get(booking_id)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404
            
        # Check authorization - only allow users to cancel their own bookings or admins to cancel any
        if booking.user_id != current_user.id and current_user.role != 'admin':
            return jsonify(success=False, message="You are not authorized to cancel this booking"), 403
            
        booking.status = "Cancelled"
        booking.walker_id = None  # Unassign walker
        db.session.commit()
        
        return jsonify(success=True, message="Booking successfully cancelled")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error cancelling booking: {e}")
        return jsonify(success=False, message="Server error"), 500
