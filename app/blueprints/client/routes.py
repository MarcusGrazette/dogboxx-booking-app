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
from app.utils.uploads import process_dog_photo, process_cropped_photo
from app.utils.booking_access import get_accessible_dog_ids, user_can_access_booking
from app.capacity import check_availability, get_slot_availability_summary, auto_assign_walker
from app.forms import OnboardingForm, BookingForm, ProfileForm
import logging
import traceback
from datetime import datetime, timezone, timedelta, date as date_type

from app.blueprints.client import client_bp
from app.utils.notifications import create_notification


def _maybe_auto_confirm(booking, dog, service_slug='group-walk'):
    """Try to auto-assign a walker to a newly-created 'requested' booking.

    If a walker with capacity is available, sets the booking to confirmed,
    assigns the walker, and notifies the client. Otherwise notifies admins
    of the pending request. Must be called before db.session.commit().
    Returns True if auto-confirmed, False if left as requested.
    """
    walker = auto_assign_walker(booking.date, booking.slot, service_slug=service_slug)
    if walker:
        booking.walker_id = walker.id
        booking.status = 'confirmed'
        booking.confirmed_at = datetime.now(timezone.utc)
        date_str = booking.date.strftime('%a %-d %b')
        create_notification(
            recipient_id=booking.user_id,
            notification_type='booking_confirmed',
            title=f'Your walk on {date_str} is confirmed',
            body=f'{dog.name} is booked for the {booking.slot} slot.',
            link='/profile',
        )
        return True
    else:
        # Notify admins — needs manual assignment
        admins = User.query.filter_by(is_admin=True).all()
        date_str = booking.date.strftime('%a %-d %b')
        for admin in admins:
            create_notification(
                recipient_id=admin.id,
                notification_type='booking_requested',
                title=f'New booking request for {date_str}',
                body=f'{booking.user.firstname} requested {booking.slot} for {dog.name}',
                link='/admin',
                sender_id=booking.user_id,
            )
        return False


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

    # Return upcoming bookings for all dogs the user has access to
    today = datetime.now(timezone.utc).date()
    _index_dog_ids = get_accessible_dog_ids(current_user.id)
    upcoming_bookings_query = Booking.query.options(
        joinedload(Booking.walker).joinedload(Walker.user),
        joinedload(Booking.service_type),
    ).filter(
        Booking.dog_id.in_(_index_dog_ids),
        Booking.status != 'cancelled',
        Booking.date >= today
    ).order_by(Booking.date.asc())

    upcoming_bookings = list(upcoming_bookings_query)
    for b in upcoming_bookings:
        if b.date:
            b.date_display = b.date.strftime("%a %d %b")
        else:
            b.date_display = None
        b.is_drop_in = b.service_type and b.service_type.slug == 'drop-in'

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

                dog = db.session.get(Dog, dog_id)
                new_booking = Booking(
                    user_id=user.id,
                    dog_id=dog_id,
                    service_type_id=default_service.id,
                    date=booking_date,
                    slot=booking_slot,
                    status=booking_status
                )
                db.session.add(new_booking)
                db.session.flush()  # get ID before notifications

                if booking_status == 'waitlisted':
                    admins = User.query.filter_by(is_admin=True).all()
                    date_str_fmt = booking_date.strftime('%a %-d %b')
                    dog_name = dog.name if dog else 'a dog'
                    for admin in admins:
                        create_notification(
                            recipient_id=admin.id,
                            notification_type='booking_requested',
                            title=f'New booking request for {date_str_fmt}',
                            body=f'{current_user.firstname} requested {booking_slot} for {dog_name}',
                            link='/admin',
                            sender_id=current_user.id,
                        )
                    db.session.commit()
                    flash(f"All slots are currently full for {booking_slot} on {booking_date.strftime('%d %b')}. "
                          f"You've been added to the waitlist — we'll let you know if a spot opens up.", "info")
                else:
                    auto_confirmed = _maybe_auto_confirm(new_booking, dog)
                    db.session.commit()
                    if auto_confirmed:
                        flash(f"Booking confirmed for {booking_slot} on {booking_date.strftime('%d %b')}!", "success")
                    else:
                        flash("Booking request submitted — we'll confirm it shortly.", "success")
                return redirect(url_for("client.index"))
    
    return render_template("index.html", user=user, client=user.client, dogs=user_dogs, bookings=upcoming_bookings, form=form) # type: ignore


@client_bp.route("/book", methods=["POST"])
@login_required
def book():
    """AJAX single booking endpoint — returns JSON, no page reload.

    Accepts JSON body: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon" }
    Returns: { "success": bool, "message": str, "booking": {...} }
    """
    data = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()
    booking_slot     = data.get('slot', '').strip()

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not booking_date_str or not booking_slot:
        return jsonify({'success': False, 'message': 'Date and slot are required.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today   = datetime.now(timezone.utc).date()
    max_date = (datetime.now(timezone.utc) + timedelta(days=90)).date()

    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400
    if booking_date > max_date:
        return jsonify({'success': False, 'message': 'Booking date cannot be more than 90 days in the future.'}), 400
    if booking_slot not in ('Morning', 'Afternoon'):
        return jsonify({'success': False, 'message': 'Invalid slot selected.'}), 400

    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account. Please add a dog before booking.'}), 400

    dog    = user_dogs[0]
    dog_id = dog.id

    # ── Duplicate / cap checks ────────────────────────────────────────────────
    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    existing = Booking.query.filter(
        Booking.dog_id   == dog_id,
        Booking.date     == booking_date,
        Booking.slot     == booking_slot,
        Booking.status.in_(active_statuses),
    ).first()
    if existing:
        return jsonify({'success': False, 'message': 'This dog already has a booking for that slot on that date.'}), 409

    day_count = Booking.query.filter(
        Booking.dog_id == dog_id,
        Booking.date   == booking_date,
        Booking.status.in_(active_statuses),
    ).count()
    if day_count >= 2:
        return jsonify({'success': False, 'message': 'This dog already has two bookings on that date.'}), 409

    # ── Capacity check + create ───────────────────────────────────────────────
    try:
        default_service = ServiceType.query.filter_by(slug='group-walk', active=True).first()
        if not default_service:
            return jsonify({'success': False, 'message': 'No service type available. Please contact support.'}), 500

        available, can_waitlist, capacity_msg = check_availability(default_service, booking_date, booking_slot)

        if not available and not can_waitlist:
            return jsonify({'success': False, 'message': capacity_msg}), 409

        booking_status = 'waitlisted' if (not available and can_waitlist) else 'requested'

        new_booking = Booking(
            user_id         = current_user.id,
            dog_id          = dog_id,
            service_type_id = default_service.id,
            date            = booking_date,
            slot            = booking_slot,
            status          = booking_status,
        )
        db.session.add(new_booking)
        db.session.flush()  # get ID before notifications

        if booking_status == 'waitlisted':
            admins = User.query.filter_by(is_admin=True).all()
            date_str_fmt = booking_date.strftime('%a %-d %b')
            for admin in admins:
                create_notification(
                    recipient_id      = admin.id,
                    notification_type = 'booking_requested',
                    title             = f'New booking request for {date_str_fmt}',
                    body              = f'{current_user.firstname} requested {booking_slot} for {dog.name}',
                    link              = '/admin',
                    sender_id         = current_user.id,
                )
            db.session.commit()
            message = (f"All slots are full — you've been added to the waitlist "
                       f"for {booking_slot} on {booking_date.strftime('%d %b')}.")
        else:
            auto_confirmed = _maybe_auto_confirm(new_booking, dog)
            db.session.commit()
            if auto_confirmed:
                booking_status = 'confirmed'
                message = f"Booking confirmed for {booking_slot} on {booking_date.strftime('%d %b')}!"
            else:
                message = 'Booking request submitted — we\'ll confirm it shortly.'

        return jsonify({
            'success': True,
            'status':  booking_status,
            'message': message,
            'booking': {
                'id':           new_booking.id,
                'date_display': booking_date.strftime('%a %-d %b'),
                'date_iso':     booking_date.isoformat(),
                'slot':         booking_slot,
                'status':       new_booking.status,
                'dog_id':       dog_id,
            },
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f'AJAX booking error for user {current_user.id}: {e}')
        return jsonify({'success': False, 'message': 'An error occurred. Please try again.'}), 500


@client_bp.route("/book_both", methods=["POST"])
@login_required
def book_both():
    """AJAX endpoint: request both Morning and Afternoon for a single date.

    Accepts JSON: { "date": "YYYY-MM-DD" }
    Each slot is booked independently — one can be requested while the other
    is waitlisted if capacity is tight. Returns both results.
    """
    data             = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()

    if not booking_date_str:
        return jsonify({'success': False, 'message': 'Date is required.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today    = datetime.now(timezone.utc).date()
    max_date = (datetime.now(timezone.utc) + timedelta(days=90)).date()
    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400
    if booking_date > max_date:
        return jsonify({'success': False, 'message': 'Booking date is too far in the future.'}), 400

    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account.'}), 400

    dog    = user_dogs[0]
    dog_id = dog.id

    default_service = ServiceType.query.filter_by(slug='group-walk', active=True).first()
    if not default_service:
        return jsonify({'success': False, 'message': 'No service type available.'}), 500

    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    created = []   # (slot, status, booking_obj)
    skipped = []   # slot names skipped (duplicate / no walkers)

    for slot in ('Morning', 'Afternoon'):
        # Skip if already booked for this slot
        if Booking.query.filter(
            Booking.dog_id == dog_id,
            Booking.date   == booking_date,
            Booking.slot   == slot,
            Booking.status.in_(active_statuses),
        ).first():
            skipped.append(slot)
            continue

        available, can_waitlist, _ = check_availability(default_service, booking_date, slot)
        if not available and not can_waitlist:
            skipped.append(slot)
            continue

        status = 'waitlisted' if (not available and can_waitlist) else 'requested'
        b = Booking(
            user_id         = current_user.id,
            dog_id          = dog_id,
            service_type_id = default_service.id,
            date            = booking_date,
            slot            = slot,
            status          = status,
        )
        db.session.add(b)
        created.append((slot, status, b))

    if not created:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'No new bookings created — slots may already be booked.'}), 409

    db.session.flush()  # get IDs before notifications

    # Auto-assign or notify admins for each booking independently
    date_str_fmt = booking_date.strftime('%a %-d %b')
    final_created = []
    pending_slots = []
    for slot, status, b in created:
        if status == 'waitlisted':
            pending_slots.append((slot, status, b))
        else:
            auto_confirmed = _maybe_auto_confirm(b, dog)
            final_created.append((slot, b.status, b))
            if not auto_confirmed:
                pending_slots.append((slot, status, b))

    # Single combined admin notification for any unconfirmed slots
    if pending_slots:
        pending_slot_names = [s for s, _, _ in pending_slots]
        slot_desc = 'both walks' if len(pending_slot_names) == 2 else pending_slot_names[0]
        for admin in User.query.filter_by(is_admin=True).all():
            create_notification(
                recipient_id      = admin.id,
                notification_type = 'booking_requested',
                title             = f'New booking request for {date_str_fmt}',
                body              = f'{current_user.firstname} requested {slot_desc} for {dog.name}',
                link              = '/admin',
                sender_id         = current_user.id,
            )

    db.session.commit()
    created = final_created if final_created else created

    # Build response
    booking_payload = []
    for slot, status, b in created:
        booking_payload.append({
            'id':           b.id,
            'date_display': booking_date.strftime('%a %-d %b'),
            'date_iso':     booking_date.isoformat(),
            'slot':         slot,
            'status':       status,
            'dog_id':       dog_id,
        })

    parts = []
    for slot, status, _ in created:
        label = 'AM' if slot == 'Morning' else 'PM'
        parts.append(f'{label} {"waitlisted" if status == "waitlisted" else "requested"}')
    if skipped:
        parts.append(f'{", ".join(skipped)} skipped (already booked)')

    return jsonify({
        'success':  True,
        'bookings': booking_payload,
        'message':  ', '.join(parts) + '.',
    })


@client_bp.route("/book_drop_in", methods=["POST"])
@login_required
def book_drop_in():
    """AJAX endpoint: request a drop-in visit for a given date + slot.

    Accepts JSON: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon" }
    Returns JSON response (success/failure + booking info).
    """
    from app.capacity import check_availability as _check

    data             = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()
    booking_slot     = data.get('slot', '').strip()

    if not booking_date_str:
        return jsonify({'success': False, 'message': 'Date is required.'}), 400
    if booking_slot not in ('Morning', 'Afternoon'):
        return jsonify({'success': False, 'message': 'Invalid slot. Choose Morning or Afternoon.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today    = datetime.now(timezone.utc).date()
    max_date = (datetime.now(timezone.utc) + timedelta(days=90)).date()
    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400
    if booking_date > max_date:
        return jsonify({'success': False, 'message': 'Booking date is too far in the future.'}), 400

    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account.'}), 400

    dog    = user_dogs[0]
    dog_id = dog.id

    drop_in_service = ServiceType.query.filter_by(slug='drop-in', active=True).first()
    if not drop_in_service:
        return jsonify({'success': False, 'message': 'Drop-in service is not currently available.'}), 503

    # Prevent duplicate
    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    existing = Booking.query.filter(
        Booking.dog_id   == dog_id,
        Booking.date     == booking_date,
        Booking.slot     == booking_slot,
        Booking.status.in_(active_statuses),
        Booking.service_type_id == drop_in_service.id,
    ).first()
    if existing:
        return jsonify({'success': False, 'message': 'A drop-in is already booked for that slot.'}), 409

    available, can_waitlist, capacity_msg = _check(drop_in_service, booking_date, booking_slot)
    if not available and not can_waitlist:
        return jsonify({'success': False, 'message': capacity_msg}), 409

    booking_status = 'waitlisted' if (not available and can_waitlist) else 'requested'

    new_booking = Booking(
        user_id         = current_user.id,
        dog_id          = dog_id,
        service_type_id = drop_in_service.id,
        date            = booking_date,
        slot            = booking_slot,
        status          = booking_status,
    )
    db.session.add(new_booking)
    db.session.flush()
    db.session.commit()

    # Notify admins
    date_str_fmt = booking_date.strftime('%a %-d %b')
    for admin in User.query.filter_by(is_admin=True).all():
        create_notification(
            recipient_id      = admin.id,
            notification_type = 'booking_requested',
            title             = f'New drop-in request for {date_str_fmt}',
            body              = f'{current_user.firstname} requested {booking_slot} drop-in for {dog.name}',
            link              = '/admin/drop-in-board',
            sender_id         = current_user.id,
        )

    if booking_status == 'waitlisted':
        message = (f"All drop-in slots are full for {booking_slot} on "
                   f"{booking_date.strftime('%d %b')}. You've been added to the waitlist.")
    else:
        message = "Drop-in request submitted — we'll confirm shortly."

    return jsonify({
        'success':  True,
        'status':   booking_status,
        'message':  message,
        'booking':  {
            'id':           new_booking.id,
            'date_display': booking_date.strftime('%a %-d %b'),
            'date_iso':     booking_date.isoformat(),
            'slot':         booking_slot,
            'status':       booking_status,
            'is_drop_in':   True,
        },
    })


@client_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Display and manage client profile, address, notifications and dog info."""
    if current_user.role != 'client':
        return redirect(url_for('client.index'))

    client = Client.query.filter_by(user_id=current_user.id).first()

    # Get primary dog (user is the main owner — can edit photo/details)
    dog_owner = DogOwner.query.filter_by(user_id=current_user.id, role='primary').first()

    # Secondary-only owners (co-owners with no primary dog of their own) should be
    # allowed to view/edit their profile without going through onboarding.
    is_secondary_only = (dog_owner is None and
                         DogOwner.query.filter_by(user_id=current_user.id, role='secondary').count() > 0)

    if not is_secondary_only and (not client or not client.onboarding_completed):
        return redirect(url_for('client.onboard'))
    dog = db.session.get(Dog, dog_owner.dog_id) if dog_owner else None

    # Get secondary dogs (user has shared access — read-only on the profile)
    secondary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='secondary').all()
    secondary_dogs = []
    for so in secondary_ownerships:
        secondary_dog = db.session.get(Dog, so.dog_id)
        if not secondary_dog:
            continue
        primary_o = DogOwner.query.filter_by(dog_id=so.dog_id, role='primary').first()
        primary_user = db.session.get(User, primary_o.user_id) if primary_o else None
        secondary_dogs.append({'dog': secondary_dog, 'primary_owner': primary_user})

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
    confirmed_this_month = sum(1 for b in month_bookings if b.status in ('confirmed', 'completed'))
    pending_this_month = sum(1 for b in month_bookings if b.status in ('requested', 'waitlisted'))

    next_booking = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.date >= today_date,
        Booking.status == 'confirmed'
    ).order_by(Booking.date).first()

    total_completed = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
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
                        return render_template("profile.html", form=form, dog=dog, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))
                    except Exception as e:
                        logging.error(f"Error processing uploaded file: {e}")
                        flash("Error processing your image. Please try a different file.", "error")
                        return render_template("profile.html", form=form, dog=dog, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))

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
        if client and client.street_address:
            address_lines = client.street_address.split('\n')
            form.address_line_1.data = address_lines[0] if len(address_lines) > 0 else ''
            form.address_line_2.data = address_lines[1] if len(address_lines) > 1 else ''
            form.address_line_3.data = address_lines[2] if len(address_lines) > 2 else ''
        if client:
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

    return render_template("profile.html", form=form, dog=dog, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))


@client_bp.route("/profile/upload-dog-photo", methods=["POST"])
@login_required
def upload_dog_photo():
    """AJAX endpoint: accept a cropped image blob and save it as the dog's photo.

    Expects a multipart POST with a 'file' field containing the canvas blob
    from Cropper.js. Returns JSON {success, url} or {success, error}.
    """
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
        logging.error(f"Error saving cropped dog photo for {current_user.email}: {e}")
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
        logging.error(f"Error saving profile photo for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error saving photo"), 500


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
            
        booking = db.session.get(Booking, booking_id)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404
            
        # Check authorization — allow booking creator, any dog owner, or admins
        if not user_can_access_booking(current_user, booking):
            return jsonify(success=False, message="You are not authorized to cancel this booking"), 403

        is_admin_cancel = current_user.is_admin and booking.user_id != current_user.id
        booking.status = "cancelled"
        booking.cancelled_at = datetime.now(timezone.utc)
        booking.cancelled_by = 'admin' if is_admin_cancel else 'client'
        booking.walker_id = None  # Unassign walker
        db.session.commit()

        date_str_fmt = booking.date.strftime('%a %-d %b')
        dog_name = booking.dog.name if booking.dog else 'Unknown dog'

        if is_admin_cancel:
            # Notify the client their walk was cancelled by admin
            create_notification(
                recipient_id=booking.user_id,
                notification_type='booking_cancelled',
                title=f"{dog_name}'s walk on {date_str_fmt} has been cancelled",
                body=booking.slot,
                link=f'/bookings/{booking.id}',
                sender_id=current_user.id,
            )
        else:
            # Notify all admins that a client cancelled
            admins = User.query.filter_by(is_admin=True).all()
            client_name = current_user.full_name
            for admin in admins:
                create_notification(
                    recipient_id=admin.id,
                    notification_type='booking_cancelled',
                    title=f"{client_name} cancelled {dog_name}'s walk",
                    body=f"{date_str_fmt} · {booking.slot}",
                    link=f'/admin/clients/{booking.user_id}',
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

    accessible_dog_ids = get_accessible_dog_ids(current_user.id)
    bookings = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
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
        end_date    (str)  'YYYY-MM-DD' — max 4 weeks from start_date (client limit)
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
        max_end = start_date + timedelta(weeks=4)

        if start_date < tomorrow:
            return jsonify(success=False, message="Start date must be in the future"), 400
        if end_date > max_end:
            return jsonify(success=False, message="End date must be within 4 weeks of the start date"), 400
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

    booking = db.session.get(Booking, booking_id)
    if not booking:
        return jsonify(success=False, message="Booking not found"), 404

    if not user_can_access_booking(current_user, booking):
        return jsonify(success=False, message="Not your booking"), 403

    data = request.get_json(silent=True) or {}
    note = (data.get('note') or '').strip()
    if len(note) > 500:
        return jsonify(success=False, message="Note must be 500 characters or fewer"), 400

    booking.client_notes = note or None
    db.session.commit()
    return jsonify(success=True, note=booking.client_notes)
