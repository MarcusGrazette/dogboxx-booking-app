"""
Admin routes.

This module defines routes for admin functionality, including dashboard, booking
management, and user management.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models import User, Booking, Walker, Dog, Client, WalkerSchedule, DogOwner
from app import db
from app.utils.db_error_handler import handle_db_errors
from app.forms import ClientCreateForm, WalkerCreateForm, WalkerScheduleForm
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
import secrets
import logging
import traceback
import json

from app.blueprints.admin import admin_bp


@admin_bp.route("/")
@login_required
def dashboard():
    """Admin dashboard page"""
    if current_user.role != 'admin':
        flash("Only admins can access.", "danger")
        return redirect(url_for("client.index"))
        
    return render_template("admin.html")


@admin_bp.route("/bookings_by_date")
@login_required
def bookings_by_date():
    """Return HTML fragment of drag-and-drop booking allocation interface (admin only)."""
    if not current_user.is_admin:
        return "Forbidden", 403

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
        
        # Separate pending and assigned bookings
        pending_bookings = [b for b in all_bookings if b.status == 'requested']
        assigned_bookings = [b for b in all_bookings if b.walker_id is not None and b.status == 'confirmed']
        
        # Add display properties to all bookings
        for b in all_bookings:
            b.dog_name = b.dog.name if b.dog else "Unknown"
            b.dog_pic = b.dog.pic if b.dog and b.dog.pic else None
            if b.walker and hasattr(b.walker, 'user'):
                b.walker_name = f"{b.walker.user.firstname}" if b.walker.user else None

        # Get all walkers with their associated user data
        walkers = Walker.query.options(joinedload(Walker.user)).all()
        
        # Create walker capacity tracking
        walker_capacity = {}
        for walker in walkers:
            walker_capacity[walker.id] = {
                'Morning': 0,
                'Afternoon': 0
            }
        
        # Count assigned bookings per walker per slot
        for booking in assigned_bookings:
            if booking.walker_id and booking.slot:
                walker_capacity[booking.walker_id][booking.slot] += 1
        
        # Generate the drag-and-drop HTML interface
        if not pending_bookings and not assigned_bookings:
            return '<p class="card-text"><i class="bi bi-info-circle"></i> No booking requests for the selected date. </p>'
        
        # Build the HTML for the drag and drop interface
        return render_template(
            'partials/admin_bookings_by_date.html', 
            pending_bookings=pending_bookings,
            assigned_bookings=assigned_bookings,
            walkers=walkers,
            walker_capacity=walker_capacity
        )
    except Exception as e:
        logging.error(f"Error in admin_bookings_by_date: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
        return "Server error", 500
    
    # Admin dashboard logic
    return render_template("admin.html")


@admin_bp.route("/assign_walker", methods=["POST"])
@login_required
@handle_db_errors(json_response=True, flash_message=False, custom_error_messages={
    IntegrityError: "Could not assign walker due to a data conflict.",
    OperationalError: "Database is temporarily unavailable. Please try again."
})
def assign_walker():
    """Assign a walker and slot to a booking (admin only). Returns JSON for AJAX requests."""
    if current_user.role != 'admin':
        return jsonify(success=False, message="Forbidden"), 403

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

        # Check walker capacity for the given slot and date
        if slot:
            same_slot_bookings = Booking.query.filter(
                Booking.walker_id == walker.id,
                Booking.date == booking.date,
                Booking.slot == slot,
                Booking.status != 'cancelled',
                Booking.id != booking.id  # Exclude current booking if reassigning
            ).count()
            
            if same_slot_bookings >= 6:
                return jsonify(success=False, message=f"Walker already has maximum bookings (6) for {slot} slot"), 400

        # Update walker assignment and slot
        booking.walker_id = walker.id
        booking.status = 'confirmed'
        if slot:
            booking.slot = slot
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
    
    # Generate drag-and-drop UI HTML
    html = f'<div class="booking-date"><h2>Bookings for {selected_date.strftime("%A, %d %B %Y")}</h2></div>'
    
    # Create a container for all columns
    html += '<div class="row g-4 mb-4">'
    
    # First column: Unassigned bookings
    html += '''
    <div class="col-md-3">
        <div class="card h-100">
            <div class="card-header bg-warning text-white">
                <h6 class="mb-0"><i class="bi bi-clock"></i> Unassigned</h6>
            </div>
            <div class="card-body p-2">
                <div class="booking-list" id="unassigned-list">
    '''
    
    # Add unassigned bookings
    for booking in [b for b in bookings if not b.walker_id]:
        html += f'''
        <div class="booking-card" 
             data-id="{booking.id}" 
             data-slot="{booking.slot if booking.slot else 'Unspecified'}"
             data-user="{booking.user.firstname} {booking.user.lastname}"
             data-dog="{booking.dog.name if booking.dog else 'Unknown'}"
             data-address="{booking.user.client.display_name if booking.user.client else 'No address'}"
             draggable="true">
            <div class="booking-header">
                <span class="badge rounded-pill bg-{_get_slot_color(booking.slot)}">{booking.slot if booking.slot else "Unspecified"}</span>
                <span class="fw-bold">{booking.user.firstname} {booking.user.lastname}</span>
            </div>
            <div class="booking-details">
                <div><i class="bi bi-house"></i> {booking.user.client.display_name if booking.user.client else "No address"}</div>
                <div><i class="bi bi-heart"></i> {booking.dog.name if booking.dog else "No dog"}</div>
            </div>
        </div>
        '''
    
    html += '''
                </div>
            </div>
        </div>
    </div>
    '''
    
    # Generate a column for each walker
    for walker in walkers:
        # Get assigned bookings for this walker
        assigned_bookings = [b for b in bookings if b.walker_id == walker.id]
        
        # Calculate capacity
        morning_count = walker_capacity[walker.id]['Morning']
        afternoon_count = walker_capacity[walker.id]['Afternoon']
        morning_assigned = [b for b in assigned_bookings if b.walker_id == walker.id and b.slot == 'Morning']
        afternoon_assigned = [b for b in assigned_bookings if b.walker_id == walker.id and b.slot == 'Afternoon']
        
        html += f'''
        <!-- Walker {walker.firstname} Column -->
        <div class="col-md-3">
            <div class="card h-100">
                <div class="card-header bg-primary text-white">
                    <h6 class="mb-0"><i class="bi bi-person-walking"></i> {walker.firstname}</h6>
                </div>
                <div class="card-body p-2">
                    <!-- Morning slot -->
                    <div class="booking-slot">
                        <div class="slot-header">
                            <span class="badge rounded-pill bg-success">Morning</span>
                            <span class="capacity-badge">{morning_count}/6</span>
                        </div>
                        <div class="booking-list" id="walker-{walker.id}-morning" 
                             data-walker-id="{walker.id}" 
                             data-slot="Morning">
        '''
        
        # Add morning bookings
        for booking in morning_assigned:
            html += f'''
            <div class="booking-card" 
                 data-id="{booking.id}" 
                 data-slot="{booking.slot}"
                 data-user="{booking.user.firstname} {booking.user.lastname}"
                 data-dog="{booking.dog.name if booking.dog else 'Unknown'}"
                 data-address="{booking.user.client.display_name if booking.user.client else 'No address'}"
                 draggable="true">
                <div class="booking-header">
                    <span class="badge rounded-pill bg-success">Morning</span>
                    <span class="fw-bold">{booking.user.firstname} {booking.user.lastname}</span>
                </div>
                <div class="booking-details">
                    <div><i class="bi bi-house"></i> {booking.user.client.display_name if booking.user.client else "No address"}</div>
                    <div><i class="bi bi-heart"></i> {booking.dog.name if booking.dog else "No dog"}</div>
                </div>
            </div>
            '''
            
        html += '''
                        </div>
                    </div>
                    
                    <!-- Afternoon slot -->
                    <div class="booking-slot mt-3">
                        <div class="slot-header">
                            <span class="badge rounded-pill bg-danger">Afternoon</span>
        '''
        
        html += f'''
                            <span class="capacity-badge">{afternoon_count}/6</span>
                        </div>
                        <div class="booking-list" id="walker-{walker.id}-afternoon" 
                             data-walker-id="{walker.id}" 
                             data-slot="Afternoon">
        '''
        
        # Add afternoon bookings
        for booking in afternoon_assigned:
            html += f'''
            <div class="booking-card" 
                 data-id="{booking.id}" 
                 data-slot="{booking.slot}"
                 data-user="{booking.user.firstname} {booking.user.lastname}"
                 data-dog="{booking.dog.name if booking.dog else 'Unknown'}"
                 data-address="{booking.user.client.display_name if booking.user.client else 'No address'}"
                 draggable="true">
                <div class="booking-header">
                    <span class="badge rounded-pill bg-danger">Afternoon</span>
                    <span class="fw-bold">{booking.user.firstname} {booking.user.lastname}</span>
                </div>
                <div class="booking-details">
                    <div><i class="bi bi-house"></i> {booking.user.client.display_name if booking.user.client else "No address"}</div>
                    <div><i class="bi bi-heart"></i> {booking.dog.name if booking.dog else "No dog"}</div>
                </div>
            </div>
            '''
            
        html += '''
                        </div>
                    </div>
                </div>
            </div>
        </div>
        '''
    
    # Close the container
    html += '</div>'
    
    return html


@admin_bp.route("/calendar_data/<int:year>/<int:month>")
@login_required
def calendar_data(year, month):
    """Return calendar data for the admin booking view"""
    if current_user.role != 'admin':
        return jsonify(success=False, message="Forbidden"), 403
        
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
def clients():
    """List all clients (admin only)"""
    if current_user.role != 'admin':
        flash("Only admins can access this page.", "danger")
        return redirect(url_for("client.index"))
    
    # Get all users with role='client' and their client records
    clients = (
        User.query
        .options(joinedload(User.client))
        .filter(User.role == 'client')
        .order_by(User.lastname, User.firstname)
        .all()
    )
    
    return render_template("admin_clients.html", clients=clients)


@admin_bp.route("/clients/new", methods=["GET", "POST"])
@login_required
def new_client():
    """Form to add a new client (admin only)"""
    if current_user.role != 'admin':
        flash("Only admins can access this page.", "danger")
        return redirect(url_for("client.index"))
    
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
def deactivate_client(client_id):
    """Deactivate a client (soft delete)"""
    if current_user.role != 'admin':
        return jsonify(success=False, message="Forbidden"), 403
    
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
def activate_client(client_id):
    """Reactivate a client"""
    if current_user.role != 'admin':
        return jsonify(success=False, message="Forbidden"), 403
    
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
def walkers():
    """List all walkers (admin only)"""
    if current_user.role != 'admin':
        flash("Only admins can access this page.", "danger")
        return redirect(url_for("client.index"))
    
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
def new_walker():
    """Form to add a new walker (admin only)"""
    if current_user.role != 'admin':
        flash("Only admins can access this page.", "danger")
        return redirect(url_for("client.index"))
    
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
def deactivate_walker(walker_id):
    """Deactivate a walker (soft delete)"""
    if current_user.role != 'admin':
        return jsonify(success=False, message="Forbidden"), 403
    
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
def activate_walker(walker_id):
    """Reactivate a walker"""
    if current_user.role != 'admin':
        return jsonify(success=False, message="Forbidden"), 403
    
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
    if current_user.role != 'admin':
        flash("Only admins can access this page.", "danger")
        return redirect(url_for("client.index"))
    
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
