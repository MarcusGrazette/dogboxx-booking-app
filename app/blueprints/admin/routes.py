"""
Admin routes.

This module defines routes for admin functionality, including dashboard, booking
management, and user management.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models import User, Booking, Walker, Dog, Client, WalkerSchedule, DogOwner, WalkerUnavailability, ServiceType, Notification
from app import db
from app.capacity import get_max_per_walker, get_walker_slot_count
from app.utils.db_error_handler import handle_db_errors
from app.forms import ClientCreateForm, WalkerCreateForm, WalkerScheduleForm
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
import secrets
import logging
import traceback
import json

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.utils.notifications import create_notification
from app.capacity import check_availability


@admin_bp.route("/")
@login_required
@admin_required
def index():
    """Admin dashboard page"""
    from datetime import date, timedelta
    from sqlalchemy import func

    today = date.today()

    # ── Stat cards ──────────────────────────────────────────────────────────
    pending_count = Booking.query.filter(
        Booking.status.in_(['requested', 'waitlisted'])
    ).count()

    active_clients = Client.query.join(User).filter(User.active == True).count()

    # Count distinct dogs with at least one active owner
    active_dogs = db.session.query(func.count(func.distinct(DogOwner.dog_id))).scalar()

    active_walkers = Walker.query.join(User).filter(User.active == True).count()

    # ── Next 4 weeks weekdays (Mon–Fri only) ────────────────────────────────
    weekdays = []
    d = today
    while len(weekdays) < 20:  # 4 weeks × 5 days
        if d.weekday() < 5:  # 0=Mon, 4=Fri
            weekdays.append(d)
        d += timedelta(days=1)

    end_date = weekdays[-1]

    # ── Booking chart data ───────────────────────────────────────────────────
    chart_bookings = (
        Booking.query
        .filter(
            Booking.date >= today,
            Booking.date <= end_date,
            Booking.status.in_(['confirmed', 'requested', 'waitlisted']),
            Booking.slot.in_(['Morning', 'Afternoon']),
        )
        .with_entities(Booking.date, Booking.slot, func.count(Booking.id).label('cnt'))
        .group_by(Booking.date, Booking.slot)
        .all()
    )

    # Build lookup: {date: {slot: count}}
    booking_lookup = {}
    for row in chart_bookings:
        booking_lookup.setdefault(row.date, {})
        booking_lookup[row.date][row.slot] = row.cnt

    chart_labels = [d.strftime('%a %-d %b') for d in weekdays]
    chart_morning = [booking_lookup.get(d, {}).get('Morning', 0) for d in weekdays]
    chart_afternoon = [booking_lookup.get(d, {}).get('Afternoon', 0) for d in weekdays]

    # ── Walker availability grid ─────────────────────────────────────────────
    all_walkers = Walker.query.join(User).filter(User.active == True).options(
        joinedload(Walker.user)
    ).all()

    schedules = WalkerSchedule.query.filter_by(active=True).all()
    # Build: {walker_id: {day_of_week: set of slots}}
    schedule_map = {}
    for s in schedules:
        schedule_map.setdefault(s.walker_id, {}).setdefault(s.day_of_week, set()).add(s.slot)

    unavails = WalkerUnavailability.query.filter(
        WalkerUnavailability.date >= today,
        WalkerUnavailability.date <= end_date,
    ).all()
    # Build: {walker_id: {date: set of unavailable slots}}
    unavail_map = {}
    for u in unavails:
        unavail_map.setdefault(u.walker_id, {}).setdefault(u.date, set()).add(u.slot)

    # Build grid: list of {walker, days: [{date, slots: ['Morning','Afternoon']}]}
    walker_grid = []
    for walker in all_walkers:
        days = []
        for d in weekdays:
            dow = d.weekday()
            scheduled = schedule_map.get(walker.id, {}).get(dow, set())
            blocked = unavail_map.get(walker.id, {}).get(d, set())
            available = scheduled - blocked
            days.append({'date': d, 'slots': sorted(available)})
        walker_grid.append({'walker': walker, 'days': days})

    return render_template(
        "admin.html",
        # Stats
        pending_count=pending_count,
        active_clients=active_clients,
        active_dogs=active_dogs,
        active_walkers=active_walkers,
        # Chart
        chart_labels=chart_labels,
        chart_morning=chart_morning,
        chart_afternoon=chart_afternoon,
        # Availability grid
        weekdays=weekdays,
        walker_grid=walker_grid,
    )


@admin_bp.route("/bookings")
@login_required
@admin_required
def bookings():
    """Booking allocation page — calendar + drag/drop."""
    return render_template("admin_bookings.html")


@admin_bp.route("/bookings_by_date")
@login_required
@admin_required
def bookings_by_date():
    """Return HTML fragment of drag-and-drop booking allocation interface (admin only)."""
    # Get date from query parameter
    date_str = request.args.get('date')
    if not date_str:
        return "Missing date parameter", 400

    try:
        # Parse the date string (format: YYYY-MM-DD)
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Get all bookings for the selected date (pending and assigned)
        all_bookings = (
            Booking.query
            .options(joinedload(Booking.dog), joinedload(Booking.walker), joinedload(Booking.user))
            .filter(
                Booking.date == selected_date,
                Booking.status != 'cancelled'
            )
            .all()
        )
        
        # Separate pending, waitlisted, and assigned bookings
        pending_bookings = [b for b in all_bookings if b.status in ('requested', 'waitlisted')]
        assigned_bookings = [b for b in all_bookings if b.walker_id is not None and b.status == 'confirmed']
        
        # Add display properties to all bookings
        for b in all_bookings:
            b.dog_name = b.dog.name if b.dog else "Unknown"
            b.dog_pic = b.dog.pic if b.dog and b.dog.pic else None
            if b.walker and hasattr(b.walker, 'user'):
                b.walker_name = f"{b.walker.user.firstname}" if b.walker.user else None

        # Determine which walkers are available for this date based on their schedule
        day_of_week = selected_date.weekday()  # 0=Monday, 6=Sunday
        
        schedules = WalkerSchedule.query.filter_by(
            day_of_week=day_of_week, active=True
        ).all()
        
        # Build a map of walker_id → set of available slots
        walker_available_slots = {}
        for sched in schedules:
            if sched.walker_id not in walker_available_slots:
                walker_available_slots[sched.walker_id] = set()
            walker_available_slots[sched.walker_id].add(sched.slot)

        # Query unavailabilities for this date and remove from available slots
        unavailabilities = WalkerUnavailability.query.filter_by(date=selected_date).all()
        unavail_set = set()  # set of (walker_id, slot) tuples
        for u in unavailabilities:
            unavail_set.add((u.walker_id, u.slot))
            if u.walker_id in walker_available_slots:
                walker_available_slots[u.walker_id].discard(u.slot)

        # Remove walkers with no remaining slots
        walker_available_slots = {wid: slots for wid, slots in walker_available_slots.items() if slots}

        # Only show walkers who have at least one slot on this day
        available_walker_ids = set(walker_available_slots.keys())
        walkers = (
            Walker.query
            .options(joinedload(Walker.user))
            .filter(Walker.id.in_(available_walker_ids))
            .all()
        ) if available_walker_ids else []
        
        # Create walker capacity tracking (only for available slots)
        walker_capacity = {}
        for walker in walkers:
            walker_capacity[walker.id] = {}
            for slot in walker_available_slots.get(walker.id, set()):
                walker_capacity[walker.id][slot] = 0
        
        # Count assigned bookings per walker per slot
        for booking in assigned_bookings:
            if booking.walker_id and booking.slot:
                if booking.walker_id in walker_capacity and booking.slot in walker_capacity[booking.walker_id]:
                    walker_capacity[booking.walker_id][booking.slot] += 1
        
        max_capacity = get_max_per_walker('group-walk')

        # Generate the drag-and-drop HTML interface
        if not pending_bookings and not assigned_bookings:
            return '<p class="card-text"><i class="bi bi-info-circle"></i> No booking requests for the selected date. </p>'
        
        # Build the HTML for the drag and drop interface
        return render_template(
            'partials/admin_bookings_by_date.html', 
            pending_bookings=pending_bookings,
            assigned_bookings=assigned_bookings,
            walkers=walkers,
            walker_capacity=walker_capacity,
            walker_available_slots={wid: list(slots) for wid, slots in walker_available_slots.items()},
            unavail_set=unavail_set,
            max_capacity=max_capacity
        )
    except Exception as e:
        logging.error(f"Error in admin_bookings_by_date: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@admin_bp.route("/assign_walker", methods=["POST"])
@login_required
@admin_required
@handle_db_errors(json_response=True, flash_message=False, custom_error_messages={
    IntegrityError: "Could not assign walker due to a data conflict.",
    OperationalError: "Database is temporarily unavailable. Please try again."
})
def assign_walker():
    """Assign a walker and slot to a booking (admin only). Returns JSON for AJAX requests."""
    # Accept JSON or form-encoded
    data = request.get_json(silent=True) or request.form
    booking_id = data.get("booking_id")
    walker_id = data.get("walker_id")
    slot = data.get("slot")  # New parameter for slot assignment

    try:
        if not booking_id:
            return jsonify(success=False, message="No booking ID provided"), 400

        booking = Booking.query.get(booking_id)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404

        # If walker_id is None, this is an unassignment operation
        if walker_id is None:
            booking.walker_id = None
            booking.status = "requested"
            db.session.commit()
            
            return jsonify(
                success=True, 
                message="Walker unassigned successfully", 
                booking={
                    "id": booking.id, 
                    "walker_id": None,
                    "walker_name": None,
                    "slot": booking.slot
                }
            ), 200

        # Otherwise, assign to a walker (normal flow)
        walker = Walker.query.filter_by(id=int(walker_id)).first()
        if not walker:
            return jsonify(success=False, message="Walker not found"), 404

        # Check walker is scheduled for this date+slot
        assign_slot = slot or booking.slot
        day_of_week = booking.date.weekday()
        schedule_exists = WalkerSchedule.query.filter_by(
            walker_id=walker.id,
            day_of_week=day_of_week,
            slot=assign_slot,
            active=True
        ).first()
        if not schedule_exists:
            return jsonify(success=False, message=f"{walker.user.firstname} is not scheduled for {assign_slot} on this day"), 400

        # Check walker capacity for the given slot and date
        if slot:
            max_capacity = get_max_per_walker('group-walk')
            same_slot_bookings = Booking.query.filter(
                Booking.walker_id == walker.id,
                Booking.date == booking.date,
                Booking.slot == slot,
                Booking.status != 'cancelled',
                Booking.id != booking.id  # Exclude current booking if reassigning
            ).count()
            
            if same_slot_bookings >= max_capacity:
                return jsonify(success=False, message=f"Walker already has maximum bookings ({max_capacity}) for {slot} slot"), 400

        # Update walker assignment and slot
        booking.walker_id = walker.id
        booking.status = 'confirmed'
        if slot:
            booking.slot = slot

        # 22a: notify client that their booking has been confirmed
        date_str_fmt = booking.date.strftime('%a %-d %b')
        create_notification(
            recipient_id=booking.user_id,
            notification_type='booking_confirmed',
            title=f'Your walk on {date_str_fmt} has been confirmed',
            body=f'Slot: {booking.slot}',
            link=f'/bookings/{booking.id}',
            sender_id=current_user.id,
        )

        # 22d: notify walker they've been assigned to a booking
        create_notification(
            recipient_id=walker.user_id,
            notification_type='walker_assigned',
            title=f'You have been assigned a walk on {date_str_fmt}',
            body=f'Slot: {booking.slot}',
            link=f'/walker/pickups?date={booking.date.isoformat()}',
            sender_id=current_user.id,
        )

        # Update pickup order for all bookings in this walker's slot
        pickup_order = data.get("pickup_order")  # list of booking IDs in order
        if pickup_order and isinstance(pickup_order, list):
            for idx, bid in enumerate(pickup_order, start=1):
                b = Booking.query.get(int(bid))
                if b and b.walker_id == walker.id and b.date == booking.date and b.slot == booking.slot:
                    b.pickup_order = idx

        db.session.commit()

        return jsonify(
            success=True, 
            message="Walker and slot assigned successfully", 
            booking={
                "id": booking.id, 
                "walker_id": walker.id,
                "walker_name": walker.firstname,  # Uses property method that accesses walker.user.firstname
                "slot": booking.slot
            }
        ), 200
        
    except Exception as e:
        # This will be handled by the @handle_db_errors decorator
        # This code won't be reached for database errors, only for other types of exceptions
        db.session.rollback()
        logging.error(f"Error assigning/unassigning walker: {e}")
        logging.debug(traceback.format_exc())
        return jsonify(success=False, message="Server error"), 500


@admin_bp.route("/reorder_pickups", methods=["POST"])
@login_required
@admin_required
def reorder_pickups():
    """Reorder pickup order for bookings within a walker's slot. Returns JSON."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid request"), 400

    pickup_order = data.get("pickup_order")  # list of booking IDs in desired order
    walker_id = data.get("walker_id")
    date_str = data.get("date")
    slot = data.get("slot")

    if not all([pickup_order, walker_id, date_str, slot]):
        return jsonify(success=False, message="Missing required fields"), 400

    if not isinstance(pickup_order, list) or len(pickup_order) == 0:
        return jsonify(success=False, message="Invalid pickup order"), 400

    try:
        from datetime import datetime
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        for idx, bid in enumerate(pickup_order, start=1):
            b = Booking.query.get(int(bid))
            if b and b.walker_id == int(walker_id) and b.date == selected_date and b.slot == slot:
                b.pickup_order = idx

        db.session.commit()
        return jsonify(success=True, message="Pickup order updated"), 200

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error reordering pickups: {e}")
        return jsonify(success=False, message="Server error"), 500


@admin_bp.route("/calendar_data/<int:year>/<int:month>")
@login_required
@admin_required
def calendar_data(year, month):
    """Return calendar data for the admin booking view"""
# Validate input
    try:
        start_date = datetime(year, month, 1).date()
        if month == 12:
            end_date = datetime(year + 1, 1, 1).date()
        else:
            end_date = datetime(year, month + 1, 1).date()
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400
        
    # Get bookings for the month
    bookings = Booking.query.filter(
        Booking.date >= start_date,
        Booking.date < end_date,
        Booking.status != 'cancelled'
    ).all()
    
    # Group by date
    booking_counts = {}
    pending_dates = set()  # Use a set to track unique dates with pending bookings
    
    for booking in bookings:
        date_str = booking.date.strftime('%Y-%m-%d')
        date_day = booking.date.day  # Extract just the day number
        
        if date_str not in booking_counts:
            booking_counts[date_str] = {
                'total': 0,
                'assigned': 0
            }
        booking_counts[date_str]['total'] += 1
        
        if booking.walker_id:
            booking_counts[date_str]['assigned'] += 1
        elif booking.status == 'requested':
            # Track dates with pending bookings
            pending_dates.add(date_day)
    
    # Convert the set to a list for JSON serialization
    pending_dates_list = list(pending_dates)
    
    return jsonify(success=True, data=booking_counts, pending_dates=pending_dates_list)


def _get_slot_color(slot):
    """Helper function to get the color class for a booking slot"""
    if not slot:
        return "secondary"
    elif slot == "Morning":
        return "success"
    elif slot == "Afternoon":
        return "danger"
    else:
        return "secondary"


# === CLIENT MANAGEMENT ROUTES ===

@admin_bp.route("/clients")
@login_required
@admin_required
def clients():
    """List all clients (admin only)"""
# Get all users with role='client' and their client records
    clients = (
        User.query
        .options(joinedload(User.client))
        .filter(User.role == 'client')
        .order_by(User.lastname, User.firstname)
        .all()
    )
    
    return render_template("admin_clients.html", clients=clients)


@admin_bp.route("/clients/<int:client_id>")
@login_required
@admin_required
def client_detail(client_id):
    """Show client detail with notification audit trail (admin only)"""
    user = User.query.filter(User.role == 'client', User.id == client_id).first_or_404()
    notifications = (
        Notification.query
        .filter_by(recipient_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template("admin_client_detail.html", client=user, notifications=notifications)


@admin_bp.route("/clients/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_client():
    """Form to add a new client (admin only)"""
    form = ClientCreateForm()
    
    if form.validate_on_submit():
        try:
            # Check if user already exists
            existing_user = User.query.filter_by(email=form.email.data.lower()).first()
            if existing_user:
                flash("A user with this email already exists.", "error")
                return render_template("admin_client_form.html", form=form, title="Add New Client")
            
            # Generate temporary password
            temp_password = secrets.token_urlsafe(12)
            
            # Create User record
            user = User(
                firstname=form.firstname.data.strip().title(),
                lastname=form.lastname.data.strip().title(),
                email=form.email.data.strip().lower(),
                role='client',
                hashed_password=generate_password_hash(temp_password),
                must_change_password=True
            )
            
            db.session.add(user)
            db.session.flush()  # Get user.id
            
            # Create Client record
            client = Client(user_id=user.id)
            db.session.add(client)
            
            db.session.commit()
            
            # TODO: Send welcome email with temp password
            logging.info(f"Admin {current_user.id} created client account for {user.email}")
            flash(f"Client account created successfully. Temporary password: {temp_password}", "success")
            
            return redirect(url_for('admin.clients'))
            
        except IntegrityError as e:
            db.session.rollback()
            logging.error(f"IntegrityError creating client: {e}")
            flash("A client with this email already exists.", "error")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating client: {e}")
            flash("An error occurred while creating the client.", "error")
    
    return render_template("admin_client_form.html", form=form, title="Add New Client")


@admin_bp.route("/clients/<int:client_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_client(client_id):
    """Deactivate a client (soft delete)"""
    try:
        user = User.query.filter(User.role == 'client', User.id == client_id).first()
        if not user:
            return jsonify(success=False, message="Client not found"), 404
        
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
        user = User.query.filter(User.role == 'client', User.id == client_id).first()
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


# === WALKER MANAGEMENT ROUTES ===

@admin_bp.route("/walkers")
@login_required
@admin_required
def walkers():
    """List all walkers (admin only)"""
# Get all users with role='walker' and their walker records
    walkers = (
        User.query
        .options(joinedload(User.walker))
        .filter(User.role == 'walker')
        .order_by(User.lastname, User.firstname)
        .all()
    )
    
    return render_template("admin_walkers.html", walkers=walkers)


@admin_bp.route("/walkers/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_walker():
    """Form to add a new walker (admin only)"""
    form = WalkerCreateForm()
    
    if form.validate_on_submit():
        try:
            # Check if user already exists
            existing_user = User.query.filter_by(email=form.email.data.lower()).first()
            if existing_user:
                flash("A user with this email already exists.", "error")
                return render_template("admin_walker_form.html", form=form, title="Add New Walker")
            
            # Generate temporary password
            temp_password = secrets.token_urlsafe(12)
            
            # Create User record
            user = User(
                firstname=form.firstname.data.strip().title(),
                lastname=form.lastname.data.strip().title(),
                email=form.email.data.strip().lower(),
                role='walker',
                hashed_password=generate_password_hash(temp_password),
                must_change_password=True
            )
            
            db.session.add(user)
            db.session.flush()  # Get user.id
            
            # Create Walker record
            walker = Walker(user_id=user.id)
            db.session.add(walker)
            
            db.session.commit()
            
            # TODO: Send welcome email with temp password
            logging.info(f"Admin {current_user.id} created walker account for {user.email}")
            flash(f"Walker account created successfully. Temporary password: {temp_password}", "success")
            
            return redirect(url_for('admin.walkers'))
            
        except IntegrityError as e:
            db.session.rollback()
            logging.error(f"IntegrityError creating walker: {e}")
            flash("A walker with this email already exists.", "error")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating walker: {e}")
            flash("An error occurred while creating the walker.", "error")
    
    return render_template("admin_walker_form.html", form=form, title="Add New Walker")


@admin_bp.route("/walkers/<int:walker_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_walker(walker_id):
    """Deactivate a walker (soft delete)"""
    try:
        user = User.query.filter(User.role == 'walker', User.id == walker_id).first()
        if not user:
            return jsonify(success=False, message="Walker not found"), 404
        
        user.active = False
        db.session.commit()
        
        logging.info(f"Admin {current_user.id} deactivated walker {user.id}")
        return jsonify(success=True, message="Walker deactivated successfully")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deactivating walker {walker_id}: {e}")
        return jsonify(success=False, message="Error deactivating walker"), 500


@admin_bp.route("/walkers/<int:walker_id>/activate", methods=["POST"])
@login_required
@admin_required
def activate_walker(walker_id):
    """Reactivate a walker"""
    try:
        user = User.query.filter(User.role == 'walker', User.id == walker_id).first()
        if not user:
            return jsonify(success=False, message="Walker not found"), 404
        
        user.active = True
        db.session.commit()
        
        logging.info(f"Admin {current_user.id} activated walker {user.id}")
        return jsonify(success=True, message="Walker activated successfully")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error activating walker {walker_id}: {e}")
        return jsonify(success=False, message="Error activating walker"), 500


@admin_bp.route("/walkers/<int:walker_id>/schedule", methods=["GET", "POST"])
@login_required
def walker_schedule(walker_id):
    """View/edit walker's weekly schedule"""
# Get walker
    walker = Walker.query.options(joinedload(Walker.user)).get_or_404(walker_id)
    
    form = WalkerScheduleForm()
    
    if form.validate_on_submit():
        try:
            # Clear existing schedules
            WalkerSchedule.query.filter_by(walker_id=walker_id).delete()
            
            # Add new schedules based on form data
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            for day_index, day_name in enumerate(days):
                day_form = getattr(form, day_name)
                
                if day_form.morning.data:
                    schedule = WalkerSchedule(
                        walker_id=walker_id,
                        day_of_week=day_index,
                        slot='Morning',
                        active=True
                    )
                    db.session.add(schedule)
                
                if day_form.afternoon.data:
                    schedule = WalkerSchedule(
                        walker_id=walker_id,
                        day_of_week=day_index,
                        slot='Afternoon',
                        active=True
                    )
                    db.session.add(schedule)
            
            db.session.commit()
            
            flash("Walker schedule updated successfully.", "success")
            return redirect(url_for('admin.walkers'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating walker schedule: {e}")
            flash("An error occurred while updating the schedule.", "error")
    
    # Pre-populate form with existing schedule
    existing_schedules = WalkerSchedule.query.filter_by(walker_id=walker_id, active=True).all()
    schedule_dict = {}
    for schedule in existing_schedules:
        if schedule.day_of_week not in schedule_dict:
            schedule_dict[schedule.day_of_week] = {'morning': False, 'afternoon': False}
        schedule_dict[schedule.day_of_week][schedule.slot.lower()] = True
    
    # Set form values
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for day_index, day_name in enumerate(days):
        day_form = getattr(form, day_name)
        if day_index in schedule_dict:
            day_form.morning.data = schedule_dict[day_index].get('morning', False)
            day_form.afternoon.data = schedule_dict[day_index].get('afternoon', False)
    
    return render_template("admin_walker_schedule.html", walker=walker, form=form)


# ─── Dogs ─────────────────────────────────────────────────────────────────────

@admin_bp.route("/dogs")
@login_required
@admin_required
def dogs():
    """Admin view: all dogs on the books, searchable by name."""
    search = request.args.get('q', '').strip()
    query = (
        Dog.query
        .join(DogOwner, DogOwner.dog_id == Dog.id)
        .join(User, User.id == DogOwner.user_id)
        .filter(DogOwner.role == 'primary')
        .add_columns(User.id.label('owner_user_id'),
                     User.firstname.label('owner_firstname'),
                     User.lastname.label('owner_lastname'),
                     User.email.label('owner_email'))
        .order_by(Dog.name)
    )
    if search:
        query = query.filter(Dog.name.ilike(f'%{search}%'))

    rows = query.all()
    # rows is a list of (Dog, owner_user_id, owner_firstname, owner_lastname, owner_email)
    dogs_data = [
        {
            'dog': row[0],
            'owner_user_id': row[1],
            'owner_name': f"{row[2]} {row[3]}",
            'owner_email': row[4],
        }
        for row in rows
    ]
    return render_template("admin_dogs.html", dogs_data=dogs_data, search=search)


@admin_bp.route("/book_for_dog", methods=["POST"])
@login_required
@admin_required
def book_for_dog():
    """Admin: create a single booking on behalf of a dog's owner."""
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        dog_id    = data.get('dog_id')
        user_id   = data.get('user_id')
        date_str  = data.get('date', '')
        slot      = data.get('slot', '')

        if not all([dog_id, user_id, date_str, slot]):
            return jsonify(success=False, message="Missing required fields"), 400

        from datetime import date as date_type
        try:
            booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date format"), 400

        if booking_date <= date_type.today():
            return jsonify(success=False, message="Date must be in the future"), 400

        if slot not in ('Morning', 'Afternoon'):
            return jsonify(success=False, message="Invalid slot"), 400

        dog = Dog.query.get(dog_id)
        if not dog:
            return jsonify(success=False, message="Dog not found"), 404

        # Duplicate check
        active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
        existing = Booking.query.filter(
            Booking.dog_id == dog_id,
            Booking.date == booking_date,
            Booking.slot == slot,
            Booking.status.in_(active_statuses)
        ).first()
        if existing:
            return jsonify(success=False, message="This dog already has a booking for that slot on that date"), 400

        default_service = ServiceType.query.filter_by(slug='group-walk', active=True).first()
        if not default_service:
            return jsonify(success=False, message="No service type available"), 400

        available, can_waitlist, capacity_msg = check_availability(default_service, booking_date, slot)
        if not available and not can_waitlist:
            return jsonify(success=False, message=capacity_msg), 400

        status = 'requested' if available else 'waitlisted'
        booking = Booking(
            user_id=user_id,
            dog_id=dog_id,
            service_type_id=default_service.id,
            date=booking_date,
            slot=slot,
            status=status,
        )
        db.session.add(booking)
        db.session.commit()

        return jsonify(success=True, status=status,
                       message=f"Booking {'requested' if status == 'requested' else 'waitlisted'} for {dog.name} on {booking_date.strftime('%-d %b %Y')}")

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in admin book_for_dog: {e}")
        return jsonify(success=False, message="Server error"), 500


@admin_bp.route("/recurring_for_dog", methods=["POST"])
@login_required
@admin_required
def recurring_for_dog():
    """Admin: create a recurring series of bookings on behalf of a dog's owner."""
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        dog_id     = data.get('dog_id')
        user_id    = data.get('user_id')
        start_str  = data.get('start_date', '')
        end_str    = data.get('end_date', '')
        slot       = data.get('slot', '')
        frequency  = data.get('frequency', '')

        if not all([dog_id, user_id, start_str, end_str, slot, frequency]):
            return jsonify(success=False, message="Missing required fields"), 400

        from datetime import date as date_type
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date   = datetime.strptime(end_str,   '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date format"), 400

        today = date_type.today()
        if start_date <= today:
            return jsonify(success=False, message="Start date must be in the future"), 400
        if end_date < start_date:
            return jsonify(success=False, message="End date must be after start date"), 400
        if slot not in ('Morning', 'Afternoon'):
            return jsonify(success=False, message="Invalid slot"), 400
        if frequency not in ('daily', 'weekly'):
            return jsonify(success=False, message="Invalid frequency"), 400

        dog = Dog.query.get(dog_id)
        if not dog:
            return jsonify(success=False, message="Dog not found"), 404

        default_service = ServiceType.query.filter_by(slug='group-walk', active=True).first()
        if not default_service:
            return jsonify(success=False, message="No service type available"), 400

        # Generate target dates
        delta = timedelta(days=1) if frequency == 'daily' else timedelta(weeks=1)
        target_dates = []
        current = start_date
        while current <= end_date:
            if frequency == 'daily' and current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            target_dates.append(current)
            current += delta

        if not target_dates:
            return jsonify(success=False, message="No valid dates in that range"), 400

        active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
        created = waitlisted = skipped = 0

        for d in target_dates:
            existing = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == d,
                Booking.slot == slot,
                Booking.status.in_(active_statuses)
            ).first()
            if existing:
                skipped += 1
                continue

            day_count = Booking.query.filter(
                Booking.dog_id == dog_id,
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
                user_id=user_id,
                dog_id=dog_id,
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
        return jsonify(success=True, created=created, waitlisted=waitlisted, skipped=skipped)

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in admin recurring_for_dog: {e}")
        return jsonify(success=False, message="Server error"), 500
