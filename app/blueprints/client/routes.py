"""
Client routes.

This module defines routes for client functionality, including home page, profile
management, onboarding, and booking management.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import User, Client, Dog, Booking, DogOwner, ServiceType, Walker
from app import db
from app.utils.db_error_handler import handle_db_errors, DBErrorHandler
from app.utils.uploads import process_dog_photo
from app.capacity import check_availability, get_slot_availability_summary
from app.forms import OnboardingForm, BookingForm, ProfileForm
import logging
import traceback
from datetime import datetime, timezone, timedelta, date as date_type

from app.blueprints.client import client_bp
from app.utils.notifications import create_notification


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
    upcoming_bookings_query = Booking.query.options(
        joinedload(Booking.walker).joinedload(Walker.user)
    ).filter(
        Booking.user_id == current_user.id,
        Booking.status != 'cancelled',
        Booking.date > today
    ).order_by(Booking.date.asc())

    upcoming_bookings = list(upcoming_bookings_query)
    for b in upcoming_bookings:
        if b.date:
            b.date_display = b.date.strftime("%a %d %b")
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

                # Check capacity before creating booking
                available, can_waitlist, capacity_msg = check_availability(
                    default_service, booking_date, booking_slot
                )

                if not available and not can_waitlist:
                    # No walkers scheduled at all — hard reject
                    flash(capacity_msg, "warning")
                    return redirect(url_for("client.index"))

                # Determine booking status based on capacity
                booking_status = 'requested'
                if not available and can_waitlist:
                    booking_status = 'waitlisted'

                new_booking = Booking(
                    user_id=user.id,
                    dog_id=dog_id,
                    service_type_id=default_service.id,
                    date=booking_date,
                    slot=booking_slot,
                    status=booking_status
                )
                db.session.add(new_booking)
                db.session.commit()

                # 22b: notify all admins of the new booking request
                admins = User.query.filter_by(is_admin=True).all()
                date_str_fmt = booking_date.strftime('%a %-d %b')
                dog = Dog.query.get(dog_id)
                dog_name = dog.name if dog else 'a dog'
                for admin in admins:
                    create_notification(
                        recipient_id=admin.id,
                        notification_type='booking_requested',
                        title=f'New booking request for {date_str_fmt}',
                        body=f'{current_user.firstname} requested {booking_slot} for {dog_name}',
                        link=f'/admin',
                        sender_id=current_user.id,
                    )

                if booking_status == 'waitlisted':
                    flash(f"All slots are currently full for {booking_slot} on {booking_date.strftime('%d %b')}. "
                          f"You've been added to the waitlist — we'll let you know if a spot opens up.", "info")
                else:
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

    # Booking stats for the profile sidebar
    from datetime import date
    today_date = date.today()
    month_start = date(today_date.year, today_date.month, 1)
    if today_date.month == 12:
        month_end = date(today_date.year + 1, 1, 1)
    else:
        month_end = date(today_date.year, today_date.month + 1, 1)

    month_bookings = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.date >= month_start,
        Booking.date < month_end,
        Booking.status.notin_(['cancelled', 'rejected'])
    ).all()
    confirmed_this_month = sum(1 for b in month_bookings if b.status in ('confirmed', 'completed'))
    pending_this_month = sum(1 for b in month_bookings if b.status in ('requested', 'waitlisted'))

    next_booking = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.date >= today_date,
        Booking.status == 'confirmed'
    ).order_by(Booking.date).first()

    total_completed = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.status == 'completed'
    ).count()

    booking_stats = {
        'confirmed_this_month': confirmed_this_month,
        'pending_this_month': pending_this_month,
        'total_this_month': len(month_bookings),
        'next_booking': next_booking,
        'total_completed': total_completed,
        'month_name': today_date.strftime('%B'),
    }

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
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None

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
                        return render_template("profile.html", form=form, dog=dog, client=client, booking_stats=booking_stats, today=datetime.now().strftime('%Y-%m-%d'))
                    except Exception as e:
                        logging.error(f"Error processing uploaded file: {e}")
                        flash("Error processing your image. Please try a different file.", "error")
                        return render_template("profile.html", form=form, dog=dog, client=client, booking_stats=booking_stats, today=datetime.now().strftime('%Y-%m-%d'))

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
        form.maps_url.data = client.maps_url

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

    return render_template("profile.html", form=form, dog=dog, client=client, booking_stats=booking_stats, today=datetime.now().strftime('%Y-%m-%d'))


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
    existing_dog = Dog.query.get(existing_dog_owner.dog_id) if existing_dog_owner else None

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
            client.pickup_instructions = form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None
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

            # Handle file upload
            pic_filename = None
            if 'file' in request.files:
                try:
                    pic_filename = process_dog_photo(request.files['file'])
                except ValueError as e:
                    logging.error(f"Invalid file upload: {e}")
                    flash(f"Upload error: {str(e)}. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, existing_dog=existing_dog, today=datetime.now().strftime('%Y-%m-%d'))
                except Exception as e:
                    logging.error(f"Error processing uploaded file: {e}")
                    flash("There was an error processing your image. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, existing_dog=existing_dog, today=datetime.now().strftime('%Y-%m-%d'))

            # Dog: update existing record if admin already created one, else create fresh
            dog_name = form.dog_name.data.strip()
            dog_gender = form.dog_gender.data.strip()
            dog_dob = form.dog_dob.data
            dog_breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
            dog_allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""

            if existing_dog:
                existing_dog.name = dog_name
                existing_dog.gender = dog_gender
                existing_dog.breed = dog_breed
                existing_dog.allergies = dog_allergies
                existing_dog.date_of_birth = dog_dob
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
                )
                db.session.add(new_dog)
                db.session.flush()
                db.session.add(DogOwner(dog_id=new_dog.id, user_id=current_user.id, role='primary'))

            db.session.commit()

            flash(f"Welcome to our platform, {current_user.firstname}! Your profile is now complete.", "success")
            return redirect(url_for('client.index'))

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error during onboarding for user {current_user.email}: {e}")
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
            form.pickup_instructions.data = client.pickup_instructions
            form.maps_url.data = client.maps_url
        if current_user.phone:
            form.phone.data = current_user.phone
        form.notify_email.data = (current_user.notification_preference or 'email') in ('email', 'both')
        form.notify_whatsapp.data = (current_user.notification_preference or '') in ('whatsapp', 'both')
        if existing_dog:
            form.dog_name.data = existing_dog.name
            form.dog_gender.data = existing_dog.gender
            form.dog_breed.data = existing_dog.breed
            form.dog_dob.data = existing_dog.date_of_birth
            form.dog_allergies.data = existing_dog.allergies

    return render_template("onboarding.html", form=form, existing_dog=existing_dog, today=datetime.now().strftime('%Y-%m-%d'))


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
            
        is_admin_cancel = current_user.is_admin and booking.user_id != current_user.id
        booking.status = "cancelled"
        booking.walker_id = None  # Unassign walker
        db.session.commit()

        # 22c: notify the client when an admin cancels their booking
        if is_admin_cancel:
            date_str_fmt = booking.date.strftime('%a %-d %b')
            dog_name = booking.dog.name if booking.dog else 'your dog'
            create_notification(
                recipient_id=booking.user_id,
                notification_type='booking_cancelled',
                title=f"{dog_name}'s walk on {date_str_fmt} has been cancelled",
                body=booking.slot,
                link=f'/bookings/{booking.id}',
                sender_id=current_user.id,
            )

        return jsonify(success=True, message="Booking successfully cancelled")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error cancelling booking: {e}")
        return jsonify(success=False, message="Server error"), 500


@client_bp.route("/calendar_data/<int:year>/<int:month>")
@login_required
def calendar_data(year, month):
    """Return this client's bookings for a given month, for the booking calendar."""
    try:
        start_date = date_type(year, month, 1)
        end_date = date_type(year + 1, 1, 1) if month == 12 else date_type(year, month + 1, 1)
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    bookings = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.date >= start_date,
        Booking.date < end_date,
        Booking.status.notin_(['cancelled', 'rejected'])
    ).all()

    # Confirmed takes priority if multiple bookings on same day
    dates = {}
    for b in bookings:
        ds = b.date.strftime('%Y-%m-%d')
        if b.status == 'confirmed':
            dates[ds] = 'confirmed'
        elif ds not in dates:
            dates[ds] = 'pending'

    return jsonify(success=True, dates=dates)


@client_bp.route("/recurring_booking", methods=["POST"])
@login_required
def recurring_booking():
    """Create a series of bookings from a start date, end date, slot and frequency.

    POST body (JSON):
        start_date  (str)  'YYYY-MM-DD' — must be tomorrow or later
        end_date    (str)  'YYYY-MM-DD' — max 4 weeks from today (client limit)
        slot        (str)  'Morning' or 'Afternoon'
        frequency   (str)  'daily' (weekdays only) or 'weekly'

    For each date in the range:
        - Skips weekends when frequency='daily'
        - Skips dates where this dog already has an active booking in that slot
        - Skips dates where the dog already has 2 bookings (one per slot limit)
        - Books as 'requested' if capacity available, 'waitlisted' if full

    Returns JSON: { success, created, waitlisted, skipped }

    Note: the 4-week cap is a client-facing safeguard. Admins booking on behalf
    of clients via /admin/recurring_for_dog have no such cap.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        start_str = data.get('start_date', '')
        end_str = data.get('end_date', '')
        slot = data.get('slot', '')
        frequency = data.get('frequency', '')

        if not all([start_str, end_str, slot, frequency]):
            return jsonify(success=False, message="Missing required fields"), 400

        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date format"), 400

        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=1)
        max_end = today + timedelta(weeks=4)

        if start_date < tomorrow:
            return jsonify(success=False, message="Start date must be in the future"), 400
        if end_date > max_end:
            return jsonify(success=False, message="End date must be within 4 weeks from today"), 400
        if end_date < start_date:
            return jsonify(success=False, message="End date must be after start date"), 400
        if slot not in ('Morning', 'Afternoon'):
            return jsonify(success=False, message="Invalid slot"), 400
        if frequency not in ('daily', 'weekly'):
            return jsonify(success=False, message="Invalid frequency"), 400

        # Generate target dates
        target_dates = []
        delta = timedelta(days=1) if frequency == 'daily' else timedelta(weeks=1)
        current = start_date
        while current <= end_date:
            if frequency == 'daily' and current.weekday() >= 5:
                current += timedelta(days=1)
                continue  # Skip weekends for daily
            target_dates.append(current)
            current += delta

        if not target_dates:
            return jsonify(success=False, message="No valid dates in that range"), 400

        # Get dog
        user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
        if not user_dogs:
            return jsonify(success=False, message="No dog found on your account"), 400
        dog = user_dogs[0]

        default_service = ServiceType.query.filter_by(slug='group-walk', active=True).first()
        if not default_service:
            return jsonify(success=False, message="No service type available"), 400

        active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
        created = waitlisted = skipped = 0

        for d in target_dates:
            # Skip duplicates
            existing = Booking.query.filter(
                Booking.dog_id == dog.id,
                Booking.date == d,
                Booking.slot == slot,
                Booking.status.in_(active_statuses)
            ).first()
            if existing:
                skipped += 1
                continue

            # Skip if already 2 bookings that day
            day_count = Booking.query.filter(
                Booking.dog_id == dog.id,
                Booking.date == d,
                Booking.status.in_(active_statuses)
            ).count()
            if day_count >= 2:
                skipped += 1
                continue

            available, can_waitlist, _ = check_availability(default_service, d, slot)
            if not available and not can_waitlist:
                skipped += 1
                continue

            status = 'requested' if available else 'waitlisted'
            db.session.add(Booking(
                user_id=current_user.id,
                dog_id=dog.id,
                service_type_id=default_service.id,
                date=d,
                slot=slot,
                status=status,
            ))
            if status == 'waitlisted':
                waitlisted += 1
            else:
                created += 1

        db.session.commit()

        # Notify admins once for the whole batch
        total = created + waitlisted
        if total > 0:
            admins = User.query.filter_by(is_admin=True).all()
            freq_label = 'daily' if frequency == 'daily' else 'weekly'
            for admin in admins:
                create_notification(
                    recipient_id=admin.id,
                    notification_type='booking_requested',
                    title=f'Recurring booking request — {total} walks',
                    body=f'{current_user.firstname} requested {freq_label} {slot} walks for {dog.name}',
                    link='/admin',
                    sender_id=current_user.id,
                )

        return jsonify(success=True, created=created, waitlisted=waitlisted, skipped=skipped)

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error creating recurring bookings: {e}")
        return jsonify(success=False, message="Server error"), 500


@client_bp.route("/booking/<int:booking_id>/note", methods=["POST"])
@login_required
def update_booking_note(booking_id):
    """Save or clear the client note on a booking."""
    if current_user.role != 'client' and not current_user.is_admin:
        return jsonify(success=False, message="Unauthorized"), 403

    booking = Booking.query.get(booking_id)
    if not booking:
        return jsonify(success=False, message="Booking not found"), 404

    if booking.user_id != current_user.id and not current_user.is_admin:
        return jsonify(success=False, message="Not your booking"), 403

    data = request.get_json(silent=True) or {}
    note = (data.get('note') or '').strip()
    if len(note) > 500:
        return jsonify(success=False, message="Note must be 500 characters or fewer"), 400

    booking.client_notes = note or None
    db.session.commit()
    return jsonify(success=True, note=booking.client_notes)
