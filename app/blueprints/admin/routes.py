"""
Admin routes.

This module defines routes for admin functionality, including dashboard, booking
management, and user management.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models import User, Booking, Walker, Dog
from app import db
from app.utils.db_error_handler import handle_db_errors
from datetime import datetime, timezone, timedelta
import logging
import traceback
import json
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
            .options(joinedload(Booking.dog), joinedload(Booking.walker))
            .filter(
                Booking.date == selected_date,
                Booking.status != 'Cancelled'
            )
            .all()
        )
        
        # Separate pending and assigned bookings
        pending_bookings = [b for b in all_bookings if b.status == 'Pending']
        assigned_bookings = [b for b in all_bookings if b.walker_id is not None]
        
        # Add display properties to all bookings
        for b in all_bookings:
            b.dog_name = b.dog.name if b.dog else "Unknown"
            b.dog_pic = b.dog.pic if b.dog and b.dog.pic else None
            if b.walker and hasattr(b.walker, 'user'):
                b.walker_name = f"{b.walker.user.first_name}" if b.walker.user else None

        # Get all walkers
        walkers = Walker.query.all()
        
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
            booking.status = "Pending"
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
                Booking.status != 'Cancelled',
                Booking.id != booking.id  # Exclude current booking if reassigning
            ).count()
            
            if same_slot_bookings >= 6:
                return jsonify(success=False, message=f"Walker already has maximum bookings (6) for {slot} slot"), 400

        # Update walker assignment and slot
        booking.walker_id = walker.id
        booking.status = 'Confirmed'
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
        Booking.status != 'Cancelled'
    ).all()
    
    # Group by date
    booking_counts = {}
    for booking in bookings:
        date_str = booking.date.strftime('%Y-%m-%d')
        if date_str not in booking_counts:
            booking_counts[date_str] = {
                'total': 0,
                'assigned': 0
            }
        booking_counts[date_str]['total'] += 1
        if booking.walker_id:
            booking_counts[date_str]['assigned'] += 1
    
    return jsonify(success=True, data=booking_counts)


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
