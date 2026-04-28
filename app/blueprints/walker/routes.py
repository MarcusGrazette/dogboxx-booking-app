"""
Walker routes.

This module defines routes for walker functionality, including schedule management,
unavailability exceptions, and pickup lists.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import case, func
from app.models import Walker, Booking, User, WalkerUnavailability, WalkerAdHocAvailability, WalkerSchedule, Client, Dog, DogOwner, DailyMessage, ServiceType
from app import db
from datetime import datetime, timezone, timedelta, date

from app.blueprints.walker import walker_bp
from app.utils.decorators import walker_required


def _double_booked_dog_ids(selected_date):
    """Return a set of dog_ids that have confirmed bookings in BOTH morning and
    afternoon on selected_date, across all walkers."""
    rows = (
        db.session.query(Booking.dog_id)
        .filter(
            Booking.date == selected_date,
            Booking.status.in_(Booking.WALKER_STATUSES),
            Booking.dog_id.isnot(None),
        )
        .group_by(Booking.dog_id)
        .having(
            func.count(case((Booking.slot == 'Morning',   1), else_=None)) > 0,
            func.count(case((Booking.slot == 'Afternoon', 1), else_=None)) > 0,
        )
        .all()
    )
    return {row.dog_id for row in rows}


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
    """Legacy route — redirect to /walker/profile."""
    return redirect(url_for('walker.profile'))


@walker_bp.route("/profile")
@login_required
@walker_required
def profile():
    """Walker profile page: account info, photo, weekly schedule, and availability."""
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

    # All future unavailabilities and ad hoc availability — no upper date cap
    # so walkers can see entries they've added months ahead (e.g. holidays).
    today = datetime.now(timezone.utc).date()
    unavailabilities = WalkerUnavailability.query.filter(
        WalkerUnavailability.walker_id == walker.id,
        WalkerUnavailability.date >= today,
    ).order_by(WalkerUnavailability.date, WalkerUnavailability.slot).all()

    adhoc_availabilities = WalkerAdHocAvailability.query.filter(
        WalkerAdHocAvailability.walker_id == walker.id,
        WalkerAdHocAvailability.date >= today,
    ).order_by(WalkerAdHocAvailability.date, WalkerAdHocAvailability.slot).all()

    return render_template("walker_profile.html",
                           walker=walker,
                           schedule_grid=schedule_grid,
                           unavailabilities=unavailabilities,
                           adhoc_availabilities=adhoc_availabilities,
                           today=today)


@walker_bp.route("/profile/upload-photo", methods=["POST"])
@login_required
@walker_required
def upload_profile_photo():
    """AJAX endpoint: accept a cropped image blob and save as the walker's profile photo."""
    from app.utils.uploads import process_cropped_photo
    import logging

    if 'file' not in request.files:
        return jsonify(success=False, error="No file provided"), 400

    try:
        filename = process_cropped_photo(request.files['file'], subfolder='profiles')
        if not filename:
            return jsonify(success=False, error="Empty file"), 400

        current_user.profile_pic = filename
        db.session.commit()

        url = url_for('static', filename=f'uploads/profiles/{filename}')
        logging.info(f"Profile photo updated for walker {current_user.email}: {filename}")
        return jsonify(success=True, url=url)

    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error saving walker profile photo for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error saving photo"), 500


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
            Booking.status.in_(Booking.WALKER_STATUSES),
        )
        .order_by(
            Booking.slot,
            case((Booking.pickup_order.is_(None), 1), else_=0),
            Booking.pickup_order,
        )
        .all()
    )

    def _is_drop_in(b):
        return b.service_type and b.service_type.slug == ServiceType.DROP_IN

    # Order: AM drop-ins → AM walks → PM walks → PM drop-ins
    morning_drop_ins   = [b for b in bookings if b.slot == 'Morning'   and     _is_drop_in(b)]
    morning_pickups    = [b for b in bookings if b.slot == 'Morning'   and not _is_drop_in(b)]
    afternoon_pickups  = [b for b in bookings if b.slot == 'Afternoon' and not _is_drop_in(b)]
    afternoon_drop_ins = [b for b in bookings if b.slot == 'Afternoon' and     _is_drop_in(b)]

    double_booked_dog_ids = _double_booked_dog_ids(selected_date)

    daily_message = DailyMessage.query.filter_by(date=selected_date).first()

    return render_template("walker_pickups.html",
                           walker=walker,
                           selected_date=selected_date,
                           today=today,
                           morning_drop_ins=morning_drop_ins,
                           morning_pickups=morning_pickups,
                           afternoon_pickups=afternoon_pickups,
                           afternoon_drop_ins=afternoon_drop_ins,
                           has_pickups=len(bookings) > 0,
                           daily_message=daily_message,
                           double_booked_dog_ids=double_booked_dog_ids)


@walker_bp.route("/monthly-summary")
@login_required
@walker_required
def monthly_summary():
    """Walker monthly summary: walks and drop-ins delivered, grouped by slot with dog counts."""
    from collections import defaultdict

    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        flash("Walker profile not found. Please contact support.", "danger")
        return redirect(url_for('client.index'))

    today = datetime.now(timezone.utc).date()
    month_str = request.args.get('month', f'{today.year}-{today.month:02d}')
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    # Cap at current month — summaries only make sense for completed/in-progress months
    if (year, month) > (today.year, today.month):
        year, month = today.year, today.month

    month_start = date(year, month, 1)
    month_end = date(year + (month // 12), (month % 12) + 1, 1)

    bookings = (
        Booking.query
        .options(
            joinedload(Booking.service_type),
            joinedload(Booking.dog),
        )
        .filter(
            Booking.walker_id == walker.id,
            Booking.date >= month_start,
            Booking.date < month_end,
            Booking.status.in_(Booking.WALKER_STATUSES),
        )
        .order_by(Booking.date, Booking.slot)
        .all()
    )

    # Group individual dog bookings into slots: (date, slot, svc_key) → [bookings]
    groups = defaultdict(list)
    for b in bookings:
        is_drop_in = b.service_type and b.service_type.slug == ServiceType.DROP_IN
        key = (b.date, b.slot, ServiceType.DROP_IN if is_drop_in else 'walk')
        groups[key].append(b)

    # Build sorted line items (one row per slot group)
    line_items = []
    for (d, slot, svc_key) in sorted(groups.keys()):
        grp = groups[(d, slot, svc_key)]
        dogs = [b.dog for b in grp if b.dog]
        line_items.append({
            'date':      d,
            'slot':      slot,
            'is_drop_in': svc_key == ServiceType.DROP_IN,
            'dog_count': len(grp),
            'dogs':      dogs,
        })

    # Summary stats
    walk_slots       = sum(1        for item in line_items if not item['is_drop_in'])
    drop_in_visits   = sum(item['dog_count'] for item in line_items if     item['is_drop_in'])
    total_dogs_walked = sum(item['dog_count'] for item in line_items if not item['is_drop_in'])

    # Month navigation
    if month == 1:
        prev_month = f'{year - 1}-12'
    else:
        prev_month = f'{year}-{month - 1:02d}'
    if month == 12:
        next_month = f'{year + 1}-01'
    else:
        next_month = f'{year}-{month + 1:02d}'
    at_current = (year == today.year and month == today.month)

    return render_template(
        'walker_monthly_summary.html',
        walker=walker,
        month_start=month_start,
        line_items=line_items,
        walk_slots=walk_slots,
        drop_in_visits=drop_in_visits,
        total_dogs_walked=total_dogs_walked,
        prev_month=prev_month,
        next_month=next_month,
        at_current=at_current,
        today=today,
    )


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
            Booking.status.in_(Booking.WALKER_STATUSES),
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
            Booking.status.in_(Booking.WALKER_STATUSES),
        )
        .order_by(
            Booking.slot,
            case((Booking.pickup_order.is_(None), 1), else_=0),
            Booking.pickup_order,
        )
        .all()
    )

    def _is_drop_in(b):
        return b.service_type and b.service_type.slug == ServiceType.DROP_IN

    morning_drop_ins   = [b for b in bookings if b.slot == 'Morning'   and     _is_drop_in(b)]
    morning_pickups    = [b for b in bookings if b.slot == 'Morning'   and not _is_drop_in(b)]
    afternoon_pickups  = [b for b in bookings if b.slot == 'Afternoon' and not _is_drop_in(b)]
    afternoon_drop_ins = [b for b in bookings if b.slot == 'Afternoon' and     _is_drop_in(b)]

    double_booked_dog_ids = _double_booked_dog_ids(selected_date)

    daily_message = DailyMessage.query.filter_by(date=selected_date).first()

    return render_template("partials/pickup_list.html",
                           selected_date=selected_date,
                           morning_drop_ins=morning_drop_ins,
                           morning_pickups=morning_pickups,
                           afternoon_pickups=afternoon_pickups,
                           afternoon_drop_ins=afternoon_drop_ins,
                           has_pickups=len(bookings) > 0,
                           daily_message=daily_message,
                           double_booked_dog_ids=double_booked_dog_ids)





@walker_bp.route("/dogs")
@login_required
@walker_required
def dogs():
    """Dog directory — all dogs with owner contact info."""
    all_dogs = Dog.query.order_by(Dog.name).all()

    # Pre-fetch primary owners (with client) in one query to avoid N+1
    ownerships = (
        DogOwner.query
        .filter_by(role='primary')
        .options(joinedload(DogOwner.user).joinedload(User.client))
        .all()
    )
    primary_owners = {o.dog_id: o.user for o in ownerships}

    return render_template(
        "walker_dogs.html",
        dogs=all_dogs,
        primary_owners=primary_owners,
        today=date.today(),
    )
