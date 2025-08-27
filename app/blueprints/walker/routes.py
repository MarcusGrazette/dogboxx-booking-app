"""
Walker routes.

This module defines routes for walker functionality, including schedule management
and walk history.
"""

from flask import request, redirect, render_template, flash, url_for
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from app.models import Walker, Booking
from app import db
from datetime import datetime, timezone, timedelta

from app.blueprints.walker import walker_bp


@walker_bp.route("/")
@login_required
def index():
    """Walker dashboard page"""
    if current_user.role != 'walker':
        return redirect(url_for(f'{current_user.role}.index'))
        
    # Redirect to schedule page for now
    return redirect(url_for('walker.schedule'))


@walker_bp.route("/schedule", methods=["GET", "POST"])
@login_required
def schedule():
    """Display the walker's schedule"""
    if current_user.role != 'walker':
        flash("Only walkers can access this page.", "danger")
        return redirect(url_for('client.index'))
        
    # Get the walker record
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        flash("Walker profile not found. Please contact support.", "danger")
        return redirect(url_for('client.index'))
        
    # Get bookings for the next 7 days
    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=7)
    
    bookings = Booking.query.options(
        joinedload(Booking.user),
        joinedload(Booking.dog),
        joinedload(Booking.client)
    ).filter(
        Booking.walker_id == walker.id,
        Booking.date >= today,
        Booking.date <= end_date,
        Booking.status != 'Cancelled'
    ).order_by(Booking.date.asc(), Booking.slot.asc()).all()
    
    # Group bookings by date and slot
    schedule = {}
    for booking in bookings:
        date_str = booking.date.strftime('%Y-%m-%d')
        if date_str not in schedule:
            schedule[date_str] = {
                'display_date': booking.date.strftime('%A, %d %B'),
                'morning': [],
                'afternoon': []
            }
        
        if booking.slot == 'Morning':
            schedule[date_str]['morning'].append(booking)
        else:
            schedule[date_str]['afternoon'].append(booking)
    
    return render_template("walker_schedule.html", schedule=schedule, walker=walker)


@walker_bp.route("/profile")
@login_required
def profile():
    """Display and manage walker profile"""
    if current_user.role != 'walker':
        return redirect(url_for(f'{current_user.role}.profile'))
        
    # Add walker profile functionality here
    return "Walker Profile Page - Coming Soon"
