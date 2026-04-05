"""
Walker routes.

This module defines routes for walker functionality, including schedule management,
unavailability exceptions, and pickup lists.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import case
from app.models import Walker, Booking, User, WalkerUnavailability, WalkerAdHocAvailability, WalkerSchedule, Client, Dog
from app import db
from datetime import datetime, timezone, timedelta, date

from app.blueprints.walker import walker_bp
from app.utils.decorators import walker_required


@walker_bp.route("/")
@login_required
@walker_required
def index():
    """Walker dashboard page"""
    return redirect(url_for('walker.pickups'))


@walker_bp.route("/schedule")
@login_required
@walker_required
def schedule():
    """Display the walker's default weekly schedule and upcoming unavailability."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        flash("Walker profile not found. Please contact support.", "danger")
        return redirect(url_for('client.index'))

    # Get default weekly schedule
    schedules = WalkerSchedule.query.filter_by(walker_id=walker.id, active=True).order_by(
        WalkerSchedule.day_of_week, WalkerSchedule.slot
    ).all()

    # Build schedule grid: {day_of_week: {slot: True/False}}
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    schedule_grid = {}
    for i, name in enumerate(day_names):
        schedule_grid[i] = {'name': name, 'Morning': False, 'Afternoon': False}
    for s in schedules:
        schedule_grid[s.day_of_week][s.slot] = True

    # Get upcoming unavailabilities and ad hoc availability (next 60 days)
    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=60)
    unavailabilities = WalkerUnavailability.query.filter(
        WalkerUnavailability.walker_id == walker.id,
        WalkerUnavailability.date >= today,
        WalkerUnavailability.date <= end_date
    ).order_by(WalkerUnavailability.date, WalkerUnavailability.slot).all()

    adhoc_availabilities = WalkerAdHocAvailability.query.filter(
        WalkerAdHocAvailability.walker_id == walker.id,
        WalkerAdHocAvailability.date >= today,
        WalkerAdHocAvailability.date <= end_date
    ).order_by(WalkerAdHocAvailability.date, WalkerAdHocAvailability.slot).all()

    return render_template("walker_schedule.html",
                           walker=walker,
                           schedule_grid=schedule_grid,
                           unavailabilities=unavailabilities,
                           adhoc_availabilities=adhoc_availabilities,
                           today=today)


@walker_bp.route("/unavailability", methods=["POST"])
@login_required
@walker_required
def add_unavailability():
    """Add an unavailability exception. JSON endpoint."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return jsonify(success=False, message="Walker profile not found"), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    date_str = data.get('date')
    slot = data.get('slot')
    reason = data.get('reason', '').strip() or None

    if not date_str or not slot:
        return jsonify(success=False, message="Date and slot are required"), 400

    if slot not in ('Morning', 'Afternoon'):
        return jsonify(success=False, message="Invalid slot"), 400

    try:
        unavail_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date format (use YYYY-MM-DD)"), 400

    if unavail_date < datetime.now(timezone.utc).date():
        return jsonify(success=False, message="Cannot mark past dates as unavailable"), 400

    # Validate walker actually works this slot on this day
    day_of_week = unavail_date.weekday()
    has_slot = WalkerSchedule.query.filter_by(
        walker_id=walker.id, day_of_week=day_of_week, slot=slot, active=True
    ).first()
    if not has_slot:
        return jsonify(success=False, message=f"You are not scheduled for {slot} on {unavail_date.strftime('%A')}s"), 400

    # Check for duplicate
    existing = WalkerUnavailability.query.filter_by(
        walker_id=walker.id, date=unavail_date, slot=slot
    ).first()
    if existing:
        return jsonify(success=False, message="Already marked as unavailable for this date/slot"), 400

    unavail = WalkerUnavailability(
        walker_id=walker.id,
        date=unavail_date,
        slot=slot,
        reason=reason
    )
    db.session.add(unavail)
    db.session.commit()

    return jsonify(success=True, message="Unavailability added", unavailability=unavail.to_dict()), 201


@walker_bp.route("/unavailability/<int:id>", methods=["DELETE"])
@login_required
@walker_required
def delete_unavailability(id):
    """Remove an unavailability exception. Walker can only delete their own."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return jsonify(success=False, message="Walker profile not found"), 404

    unavail = db.session.get(WalkerUnavailability, id)
    if not unavail:
        return jsonify(success=False, message="Not found"), 404

    if unavail.walker_id != walker.id:
        return jsonify(success=False, message="Forbidden"), 403

    db.session.delete(unavail)
    db.session.commit()

    return jsonify(success=True, message="Unavailability removed")


@walker_bp.route("/adhoc", methods=["POST"])
@login_required
@walker_required
def add_adhoc():
    """Add an ad hoc available day outside the default schedule. JSON endpoint."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return jsonify(success=False, message="Walker profile not found"), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    date_str = data.get('date')
    slot = data.get('slot')

    if not date_str or not slot:
        return jsonify(success=False, message="Date and slot are required"), 400

    if slot not in ('Morning', 'Afternoon'):
        return jsonify(success=False, message="Invalid slot"), 400

    try:
        adhoc_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date format (use YYYY-MM-DD)"), 400

    if adhoc_date < datetime.now(timezone.utc).date():
        return jsonify(success=False, message="Cannot add availability for past dates"), 400

    # Validate walker does NOT already work this slot on this day (would be redundant)
    day_of_week = adhoc_date.weekday()
    already_scheduled = WalkerSchedule.query.filter_by(
        walker_id=walker.id, day_of_week=day_of_week, slot=slot, active=True
    ).first()
    if already_scheduled:
        return jsonify(success=False, message=f"You are already scheduled for {slot} on {adhoc_date.strftime('%A')}s"), 400

    # Check for duplicate
    existing = WalkerAdHocAvailability.query.filter_by(
        walker_id=walker.id, date=adhoc_date, slot=slot
    ).first()
    if existing:
        return jsonify(success=False, message="Already marked as available for this date/slot"), 400

    adhoc = WalkerAdHocAvailability(
        walker_id=walker.id,
        date=adhoc_date,
        slot=slot,
    )
    db.session.add(adhoc)
    db.session.commit()

    return jsonify(success=True, message="Ad hoc availability added", adhoc=adhoc.to_dict()), 201


@walker_bp.route("/adhoc/<int:id>", methods=["DELETE"])
@login_required
@walker_required
def delete_adhoc(id):
    """Remove an ad hoc availability entry. Walker can only delete their own."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return jsonify(success=False, message="Walker profile not found"), 404

    adhoc = db.session.get(WalkerAdHocAvailability, id)
    if not adhoc:
        return jsonify(success=False, message="Not found"), 404

    if adhoc.walker_id != walker.id:
        return jsonify(success=False, message="Forbidden"), 403

    db.session.delete(adhoc)
    db.session.commit()

    return jsonify(success=True, message="Ad hoc availability removed")


@walker_bp.route("/pickups")
@walker_bp.route("/pickups/<date_str>")
@login_required
@walker_required
def pickups(date_str=None):
    """Show today's pickup list (or a specific date)."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        flash("Walker profile not found. Please contact support.", "danger")
        return redirect(url_for('client.index'))

    today = datetime.now(timezone.utc).date()

    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Invalid date format.", "danger")
            return redirect(url_for('walker.pickups'))
    else:
        selected_date = today

    # Query confirmed bookings for this walker on this date
    bookings = (
        Booking.query
        .options(
            joinedload(Booking.dog),
            joinedload(Booking.user).joinedload(User.client),
            joinedload(Booking.service_type),
        )
        .filter(
            Booking.walker_id == walker.id,
            Booking.date == selected_date,
            Booking.status.in_(['confirmed', 'completed']),
        )
        .order_by(
            Booking.slot,
            case((Booking.pickup_order.is_(None), 1), else_=0),
            Booking.pickup_order,
        )
        .all()
    )

    def _is_drop_in(b):
        return b.service_type and b.service_type.slug == 'drop-in'

    # Order: AM drop-ins → AM walks → PM walks → PM drop-ins
    morning_drop_ins   = [b for b in bookings if b.slot == 'Morning'   and     _is_drop_in(b)]
    morning_pickups    = [b for b in bookings if b.slot == 'Morning'   and not _is_drop_in(b)]
    afternoon_pickups  = [b for b in bookings if b.slot == 'Afternoon' and not _is_drop_in(b)]
    afternoon_drop_ins = [b for b in bookings if b.slot == 'Afternoon' and     _is_drop_in(b)]

    return render_template("walker_pickups.html",
                           walker=walker,
                           selected_date=selected_date,
                           today=today,
                           morning_drop_ins=morning_drop_ins,
                           morning_pickups=morning_pickups,
                           afternoon_pickups=afternoon_pickups,
                           afternoon_drop_ins=afternoon_drop_ins,
                           has_pickups=len(bookings) > 0)


@walker_bp.route("/api/pickup-days/<int:year>/<int:month>")
@login_required
@walker_required
def api_pickup_days(year, month):
    """Return a JSON map of dates that have pickups for this walker in a given month."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return jsonify({}), 404

    from calendar import monthrange
    try:
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
    except ValueError:
        return jsonify({}), 400

    bookings = (
        Booking.query
        .filter(
            Booking.walker_id == walker.id,
            Booking.date >= start,
            Booking.date <= end,
            Booking.status.in_(['confirmed', 'completed']),
        )
        .with_entities(Booking.date)
        .distinct()
        .all()
    )

    dates = {b.date.strftime('%Y-%m-%d'): 'pickup' for b in bookings}
    return jsonify(dates)


@walker_bp.route("/api/pickup-list/<date_str>")
@login_required
@walker_required
def api_pickup_list(date_str):
    """Return the pickup list HTML partial for a given date."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return "Walker not found", 404

    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return "Invalid date", 400

    bookings = (
        Booking.query
        .options(
            joinedload(Booking.dog),
            joinedload(Booking.user).joinedload(User.client),
            joinedload(Booking.service_type),
        )
        .filter(
            Booking.walker_id == walker.id,
            Booking.date == selected_date,
            Booking.status.in_(['confirmed', 'completed']),
        )
        .order_by(
            Booking.slot,
            case((Booking.pickup_order.is_(None), 1), else_=0),
            Booking.pickup_order,
        )
        .all()
    )

    def _is_drop_in(b):
        return b.service_type and b.service_type.slug == 'drop-in'

    morning_drop_ins   = [b for b in bookings if b.slot == 'Morning'   and     _is_drop_in(b)]
    morning_pickups    = [b for b in bookings if b.slot == 'Morning'   and not _is_drop_in(b)]
    afternoon_pickups  = [b for b in bookings if b.slot == 'Afternoon' and not _is_drop_in(b)]
    afternoon_drop_ins = [b for b in bookings if b.slot == 'Afternoon' and     _is_drop_in(b)]

    return render_template("partials/pickup_list.html",
                           selected_date=selected_date,
                           morning_drop_ins=morning_drop_ins,
                           morning_pickups=morning_pickups,
                           afternoon_pickups=afternoon_pickups,
                           afternoon_drop_ins=afternoon_drop_ins,
                           has_pickups=len(bookings) > 0)


@walker_bp.route("/profile")
@login_required
@walker_required
def profile():
    """Display and manage walker profile"""
    return "Walker Profile Page - Coming Soon"
