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
from app.utils.uploads import process_dog_photo
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

    # ── Next 4 weeks: all 28 days for chart, weekdays only for walker grid ───
    chart_days = [today + timedelta(days=i) for i in range(28)]
    weekdays   = [d for d in chart_days if d.weekday() < 5]
    chart_end  = chart_days[-1]

    # ── Booking chart data — split by slot AND status ────────────────────────
    chart_bookings = (
        Booking.query
        .filter(
            Booking.date >= today,
            Booking.date <= chart_end,
            Booking.status.in_(['confirmed', 'requested', 'waitlisted']),
            Booking.slot.in_(['Morning', 'Afternoon']),
        )
        .with_entities(
            Booking.date,
            Booking.slot,
            Booking.status,
            func.count(Booking.id).label('cnt'),
        )
        .group_by(Booking.date, Booking.slot, Booking.status)
        .all()
    )

    # Build lookup: {date: {slot: {status: count}}}
    booking_lookup = {}
    for row in chart_bookings:
        booking_lookup.setdefault(row.date, {}).setdefault(row.slot, {})[row.status] = row.cnt

    def slot_cnt(d, slot, status):
        return booking_lookup.get(d, {}).get(slot, {}).get(status, 0)

    chart_labels              = [d.strftime('%a %-d') for d in chart_days]
    chart_is_weekend          = [1 if d.weekday() >= 5 else 0 for d in chart_days]
    chart_morning_confirmed   = [slot_cnt(d, 'Morning',   'confirmed')  for d in chart_days]
    chart_morning_pending     = [slot_cnt(d, 'Morning',   'requested')  for d in chart_days]
    chart_morning_waitlisted  = [slot_cnt(d, 'Morning',   'waitlisted') for d in chart_days]
    chart_afternoon_confirmed = [slot_cnt(d, 'Afternoon', 'confirmed')  for d in chart_days]
    chart_afternoon_pending   = [slot_cnt(d, 'Afternoon', 'requested')  for d in chart_days]
    chart_afternoon_waitlisted= [slot_cnt(d, 'Afternoon', 'waitlisted') for d in chart_days]

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
        WalkerUnavailability.date <= chart_end,
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
        today_iso=today.isoformat(),
        chart_labels=chart_labels,
        chart_is_weekend=chart_is_weekend,
        chart_morning_confirmed=chart_morning_confirmed,
        chart_morning_pending=chart_morning_pending,
        chart_morning_waitlisted=chart_morning_waitlisted,
        chart_afternoon_confirmed=chart_afternoon_confirmed,
        chart_afternoon_pending=chart_afternoon_pending,
        chart_afternoon_waitlisted=chart_afternoon_waitlisted,
        # Availability grid
        weekdays=weekdays,
        walker_grid=walker_grid,
    )


@admin_bp.route("/api/chart-data")
@login_required
@admin_required
def chart_data():
    """Return 28-day booking chart data as JSON starting from ?start=YYYY-MM-DD.
    Start date is clamped to today at the minimum."""
    from datetime import date, timedelta
    from sqlalchemy import func

    today = date.today()

    start_str = request.args.get('start')
    try:
        start = date.fromisoformat(start_str)
    except (TypeError, ValueError):
        start = today

    # Never allow scrolling before today
    if start < today:
        start = today

    chart_days = [start + timedelta(days=i) for i in range(28)]
    chart_end  = chart_days[-1]

    chart_bookings = (
        Booking.query
        .filter(
            Booking.date >= start,
            Booking.date <= chart_end,
            Booking.status.in_(['confirmed', 'requested', 'waitlisted']),
            Booking.slot.in_(['Morning', 'Afternoon']),
        )
        .with_entities(
            Booking.date,
            Booking.slot,
            Booking.status,
            func.count(Booking.id).label('cnt'),
        )
        .group_by(Booking.date, Booking.slot, Booking.status)
        .all()
    )

    booking_lookup = {}
    for row in chart_bookings:
        booking_lookup.setdefault(row.date, {}).setdefault(row.slot, {})[row.status] = row.cnt

    def slot_cnt(d, slot, status):
        return booking_lookup.get(d, {}).get(slot, {}).get(status, 0)

    return jsonify(
        start=start.isoformat(),
        labels=[d.strftime('%a %-d %b') for d in chart_days],
        is_weekend=[1 if d.weekday() >= 5 else 0 for d in chart_days],
        morning_confirmed=[slot_cnt(d, 'Morning',   'confirmed')  for d in chart_days],
        morning_pending=[slot_cnt(d, 'Morning',   'requested')  for d in chart_days],
        morning_waitlisted=[slot_cnt(d, 'Morning',   'waitlisted') for d in chart_days],
        afternoon_confirmed=[slot_cnt(d, 'Afternoon', 'confirmed')  for d in chart_days],
        afternoon_pending=[slot_cnt(d, 'Afternoon', 'requested')  for d in chart_days],
        afternoon_waitlisted=[slot_cnt(d, 'Afternoon', 'waitlisted') for d in chart_days],
    )


@admin_bp.route("/api/board-chart-data")
@login_required
@admin_required
def board_chart_data():
    """Return 7-day booking chart data for the week (Mon–Sun) containing ?date=YYYY-MM-DD."""
    from datetime import date, timedelta
    from sqlalchemy import func

    date_str = request.args.get('date')
    try:
        selected = date.fromisoformat(date_str)
    except (TypeError, ValueError):
        selected = date.today()

    # If weekend selected, advance to next Monday and show that week
    if selected.weekday() >= 5:
        days_to_monday = 7 - selected.weekday()
        week_start = selected + timedelta(days=days_to_monday)
        selected_index = None   # selected day isn't in the chart
    else:
        week_start = selected - timedelta(days=selected.weekday())
        selected_index = selected.weekday()  # 0=Mon … 4=Fri

    # Weekdays only (Mon–Fri)
    chart_days = [week_start + timedelta(days=i) for i in range(5)]
    chart_end  = chart_days[-1]

    chart_bookings = (
        Booking.query
        .filter(
            Booking.date >= week_start,
            Booking.date <= chart_end,
            Booking.status.in_(['confirmed', 'requested', 'waitlisted']),
            Booking.slot.in_(['Morning', 'Afternoon']),
        )
        .with_entities(
            Booking.date,
            Booking.slot,
            Booking.status,
            func.count(Booking.id).label('cnt'),
        )
        .group_by(Booking.date, Booking.slot, Booking.status)
        .all()
    )

    booking_lookup = {}
    for row in chart_bookings:
        booking_lookup.setdefault(row.date, {}).setdefault(row.slot, {})[row.status] = row.cnt

    def slot_cnt(d, slot, status):
        return booking_lookup.get(d, {}).get(slot, {}).get(status, 0)

    return jsonify(
        week_start=week_start.isoformat(),
        week_end=chart_days[-1].isoformat(),
        selected=selected.isoformat(),
        selected_index=selected_index,       # 0=Mon … 4=Fri, or null if weekend
        labels=[d.strftime('%a %-d') for d in chart_days],
        is_weekend=[0] * 5,                  # always weekdays
        morning_confirmed=  [slot_cnt(d, 'Morning',   'confirmed')  for d in chart_days],
        morning_pending=    [slot_cnt(d, 'Morning',   'requested')  for d in chart_days],
        morning_waitlisted= [slot_cnt(d, 'Morning',   'waitlisted') for d in chart_days],
        afternoon_confirmed=[slot_cnt(d, 'Afternoon', 'confirmed')  for d in chart_days],
        afternoon_pending=  [slot_cnt(d, 'Afternoon', 'requested')  for d in chart_days],
        afternoon_waitlisted=[slot_cnt(d,'Afternoon', 'waitlisted') for d in chart_days],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Revenue helpers
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_for_range(start, end):
    """Return a list of daily revenue dicts for start..end (inclusive).

    Each dict: {date, revenue, walks, doubles, price_per_walk, discount}

    Logic per day:
      - Count confirmed bookings by (dog_id, slot)
      - A dog with BOTH Morning + Afternoon on the same day gets one discount
      - revenue = walks * price_per_walk - doubles * double_slot_discount
    Uses the PricingConfig with the highest effective_from <= that day.
    """
    from datetime import timedelta
    from sqlalchemy import func
    from app.models import PricingConfig, Booking, DogOwner

    # Load all relevant pricing configs once
    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    def config_for(d):
        for c in all_configs:
            if c.effective_from <= d:
                return c
        return None  # no pricing configured before this date

    # Query confirmed bookings in range — get (date, dog_id, slot) tuples
    rows = (
        Booking.query
        .filter(
            Booking.date >= start,
            Booking.date <= end,
            Booking.status == 'confirmed',
            Booking.slot.in_(['Morning', 'Afternoon']),
        )
        .with_entities(Booking.date, Booking.dog_id, Booking.slot)
        .all()
    )

    # Build lookup: {date: {dog_id: set(slots)}}
    day_dog_slots = {}
    for r in rows:
        day_dog_slots.setdefault(r.date, {}).setdefault(r.dog_id, set()).add(r.slot)

    results = []
    d = start
    while d <= end:
        dog_slots = day_dog_slots.get(d, {})
        walks   = sum(len(slots) for slots in dog_slots.values())
        doubles = sum(1 for slots in dog_slots.values()
                      if 'Morning' in slots and 'Afternoon' in slots)
        cfg = config_for(d)
        if cfg:
            price    = float(cfg.price_per_walk)
            discount = float(cfg.double_slot_discount)
            revenue  = round(walks * price - doubles * discount, 2)
        else:
            price = discount = revenue = 0.0
        results.append({
            'date':           d,
            'revenue':        revenue,
            'walks':          walks,
            'doubles':        doubles,
            'price_per_walk': price,
            'discount':       discount,
        })
        d += timedelta(days=1)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Revenue page
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route("/revenue")
@login_required
@admin_required
def revenue():
    """Revenue tracker page."""
    from datetime import date
    from app.models import PricingConfig

    today = date.today()
    # Default: current calendar month
    start = today.replace(day=1)
    end   = (start.replace(month=start.month % 12 + 1, day=1)
             if start.month < 12
             else start.replace(year=start.year + 1, month=1, day=1))
    import datetime as _dt
    end = end - _dt.timedelta(days=1)

    daily = _revenue_for_range(start, end)

    current_pricing = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= today)
        .order_by(PricingConfig.effective_from.desc())
        .first()
    )
    all_pricing = (
        PricingConfig.query
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    return render_template(
        "admin_revenue.html",
        today_iso=today.isoformat(),
        start_iso=start.isoformat(),
        chart_labels=[r['date'].strftime('%-d') for r in daily],
        chart_revenue=[r['revenue'] for r in daily],
        chart_walks=[r['walks'] for r in daily],
        total_revenue=sum(r['revenue'] for r in daily),
        total_walks=sum(r['walks'] for r in daily),
        total_doubles=sum(r['doubles'] for r in daily),
        current_pricing=current_pricing,
        all_pricing=all_pricing,
    )


@admin_bp.route("/api/revenue-data")
@login_required
@admin_required
def revenue_data():
    """JSON revenue data for a calendar month. ?start=YYYY-MM-DD (any day in the month)."""
    from datetime import date
    import datetime as _dt

    today = date.today()
    start_str = request.args.get('start')
    try:
        raw = date.fromisoformat(start_str)
    except (TypeError, ValueError):
        raw = today

    start = raw.replace(day=1)
    end   = (start.replace(month=start.month % 12 + 1, day=1)
             if start.month < 12
             else start.replace(year=start.year + 1, month=1, day=1))
    end = end - _dt.timedelta(days=1)

    daily = _revenue_for_range(start, end)

    from app.models import PricingConfig
    current_pricing = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= today)
        .order_by(PricingConfig.effective_from.desc())
        .first()
    )

    return jsonify(
        start=start.isoformat(),
        labels=[r['date'].strftime('%-d') for r in daily],
        month_label=start.strftime('%B %Y'),
        revenue=[r['revenue'] for r in daily],
        walks=[r['walks'] for r in daily],
        total_revenue=round(sum(r['revenue'] for r in daily), 2),
        total_walks=sum(r['walks'] for r in daily),
        total_doubles=sum(r['doubles'] for r in daily),
        current_pricing=current_pricing.to_dict() if current_pricing else None,
    )


@admin_bp.route("/revenue/pricing", methods=["POST"])
@login_required
@admin_required
def update_pricing():
    """Add a new pricing tier."""
    from datetime import date
    from app.models import PricingConfig

    try:
        price    = float(request.form['price_per_walk'])
        discount = float(request.form['double_slot_discount'])
        eff_from = date.fromisoformat(request.form['effective_from'])
    except (KeyError, ValueError) as e:
        flash(f"Invalid pricing data: {e}", "danger")
        return redirect(url_for('admin.revenue'))

    # Check for duplicate effective_from
    existing = PricingConfig.query.filter_by(effective_from=eff_from).first()
    if existing:
        existing.price_per_walk       = price
        existing.double_slot_discount = discount
        flash(f"Pricing for {eff_from} updated.", "success")
    else:
        db.session.add(PricingConfig(
            price_per_walk=price,
            double_slot_discount=discount,
            effective_from=eff_from,
        ))
        flash(f"New pricing tier effective from {eff_from} added.", "success")

    db.session.commit()
    return redirect(url_for('admin.revenue'))


@admin_bp.route("/board")
@login_required
@admin_required
def board():
    """New assignment board — click-to-assign + drag-to-reorder."""
    return render_template("admin_board.html")


@admin_bp.route("/api/board-data/<date_str>")
@login_required
@admin_required
def board_data(date_str):
    """JSON board data for a given date — pending bookings, walkers, assignments."""
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    all_bookings = (
        Booking.query
        .options(
            joinedload(Booking.dog),
            joinedload(Booking.walker).joinedload(Walker.user),
            joinedload(Booking.user),
        )
        .filter(Booking.date == selected_date, Booking.status != 'cancelled')
        .all()
    )

    day_of_week = selected_date.weekday()
    schedules = WalkerSchedule.query.filter_by(day_of_week=day_of_week, active=True).all()
    walker_available_slots = {}
    for s in schedules:
        walker_available_slots.setdefault(s.walker_id, set()).add(s.slot)

    unavailabilities = WalkerUnavailability.query.filter_by(date=selected_date).all()
    for u in unavailabilities:
        if u.walker_id in walker_available_slots:
            walker_available_slots[u.walker_id].discard(u.slot)
    walker_available_slots = {wid: slots for wid, slots in walker_available_slots.items() if slots}

    walkers = (
        Walker.query.options(joinedload(Walker.user))
        .filter(Walker.id.in_(walker_available_slots.keys()))
        .all()
    ) if walker_available_slots else []

    def booking_dict(b, include_walker=False):
        d = {
            'id': b.id,
            'dog_name': b.dog.name if b.dog else 'Unknown',
            'dog_pic': b.dog.pic if b.dog and b.dog.pic else None,
            'owner_name': b.user.full_name if b.user else '',
            'slot': b.slot,
            'status': b.status,
            'pickup_order': b.pickup_order,
            'walker_id': b.walker_id,
        }
        return d

    pending   = [booking_dict(b) for b in all_bookings if b.status in ('requested', 'waitlisted')]
    assigned  = [booking_dict(b) for b in all_bookings if b.walker_id and b.status == 'confirmed']

    walkers_data = [
        {
            'id': w.id,
            'name': w.user.firstname if w.user else 'Walker',
            'available_slots': sorted(walker_available_slots.get(w.id, []), key=lambda s: 0 if s == 'Morning' else 1),
        }
        for w in walkers
    ]

    max_capacity = get_max_per_walker('group-walk')

    return jsonify(
        success=True,
        date=date_str,
        pending=pending,
        assigned=assigned,
        walkers=walkers_data,
        max_capacity=max_capacity,
    )


@admin_bp.route("/assign_walker", methods=["POST"])
@login_required
@admin_required
@handle_db_errors(json_response=True, flash_message=False, custom_error_messages={
    IntegrityError: "Could not assign walker due to a data conflict.",
    OperationalError: "Database is temporarily unavailable. Please try again."
})
def assign_walker():
    """Assign (or unassign) a walker and slot to a booking. Admin only. Returns JSON.

    POST body (JSON or form-encoded):
        booking_id  (int)   Required. Booking to update.
        walker_id   (int)   Walker to assign. Omit or null to unassign.
        slot        (str)   'Morning' or 'Afternoon'. Overrides booking.slot if provided.
        pickup_order (list) Optional. List of booking IDs in pickup order for this
                            walker/date/slot — persists pickup_order on each booking.

    Side effects on successful assignment:
        - Sets booking.status = 'confirmed', booking.walker_id, booking.slot
        - Sends in-app notification to client (booking_confirmed)
        - Sends in-app notification to walker (walker_assigned)
        - Persists pickup_order if provided

    On unassignment (walker_id = null):
        - Clears booking.walker_id, sets status back to 'requested'
    """
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
        dog_name = booking.dog.name if booking.dog else 'your dog'
        create_notification(
            recipient_id=booking.user_id,
            notification_type='booking_confirmed',
            title=f"{dog_name}'s walk on {date_str_fmt} has been confirmed",
            body=booking.slot,
            link=f'/bookings/{booking.id}',
            sender_id=current_user.id,
        )

        # 22d: notify walker they've been assigned to a booking
        create_notification(
            recipient_id=walker.user_id,
            notification_type='walker_assigned',
            title=f'You have been assigned a walk on {date_str_fmt}',
            body=f'{dog_name} — {booking.slot}',
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
    """Show client detail with dog info and notification audit trail (admin only)"""
    user = User.query.filter(User.role == 'client', User.id == client_id).first_or_404()
    dog_owner = DogOwner.query.filter_by(user_id=user.id, role='primary').first()
    dog = Dog.query.get(dog_owner.dog_id) if dog_owner else None
    notifications = (
        Notification.query
        .filter_by(recipient_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template("admin_client_detail.html", client=user, dog=dog, notifications=notifications)


@admin_bp.route("/clients/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_client():
    """Create a new client account with full details (admin only).

    The admin can fill in address, pickup notes, and dog info upfront so that
    the client sees their profile (and any pre-created bookings) the moment
    they first log in.  Onboarding is marked complete automatically when both
    address and dog info are provided; otherwise the client will still be
    prompted to complete the remaining steps on first login.
    """
    form = ClientCreateForm()

    if form.validate_on_submit():
        try:
            existing_user = User.query.filter_by(email=form.email.data.lower()).first()
            if existing_user:
                flash("A user with this email already exists.", "error")
                return render_template("admin_client_form.html", form=form, title="Add New Client", is_edit=False)

            temp_password = secrets.token_urlsafe(12)

            user = User(
                firstname=form.firstname.data.strip().title(),
                lastname=form.lastname.data.strip().title(),
                email=form.email.data.strip().lower(),
                role='client',
                hashed_password=generate_password_hash(temp_password),
                must_change_password=True,
            )
            if form.notify_email.data and form.notify_whatsapp.data:
                user.notification_preference = 'both'
            elif form.notify_whatsapp.data:
                user.notification_preference = 'whatsapp'
            else:
                user.notification_preference = 'email'
            user.phone = form.phone.data.strip() if form.phone.data else None

            db.session.add(user)
            db.session.flush()  # get user.id

            # Build Client record
            client = Client(user_id=user.id)
            has_address = bool(form.address_line_1.data and form.address_line_1.data.strip())
            if has_address:
                client.street_address = form.address_line_1.data.strip()
                if form.address_line_2.data and form.address_line_2.data.strip():
                    client.street_address += '\n' + form.address_line_2.data.strip()
                if form.address_line_3.data and form.address_line_3.data.strip():
                    client.street_address += '\n' + form.address_line_3.data.strip()
                client.postal_code = form.postcode.data.strip() if form.postcode.data else None
            client.pickup_instructions = form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None
            db.session.add(client)
            db.session.flush()  # get client.id

            # Create Dog record if core dog fields are present
            has_dog = bool(form.dog_name.data and form.dog_gender.data and form.dog_dob.data)
            if has_dog:
                new_dog = Dog(
                    name=form.dog_name.data.strip(),
                    gender=form.dog_gender.data,
                    breed=form.dog_breed.data.strip() if form.dog_breed.data else "",
                    allergies=form.dog_allergies.data.strip() if form.dog_allergies.data else "",
                    date_of_birth=form.dog_dob.data,
                )
                db.session.add(new_dog)
                db.session.flush()
                db.session.add(DogOwner(dog_id=new_dog.id, user_id=user.id, role='primary'))

            # Mark onboarding complete when the admin has provided everything
            if has_address and has_dog:
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)

            db.session.commit()

            logging.info(f"Admin {current_user.id} created client account for {user.email} "
                         f"(address={'yes' if has_address else 'no'}, dog={'yes' if has_dog else 'no'})")

            flash(
                f"Client account created. "
                f"Temporary password: <strong>{temp_password}</strong> — share this with {user.firstname}.",
                "success"
            )
            return redirect(url_for('admin.client_detail', client_id=user.id))

        except IntegrityError as e:
            db.session.rollback()
            logging.error(f"IntegrityError creating client: {e}")
            flash("A client with this email already exists.", "error")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating client: {e}")
            flash("An error occurred while creating the client.", "error")

    return render_template("admin_client_form.html", form=form, title="Add New Client", is_edit=False)


@admin_bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_client(client_id):
    """Edit an existing client's details (admin only).

    Updates name, address, pickup notes, notification preferences, and dog
    info.  Will create a dog record if one doesn't exist yet.  Marks
    onboarding complete automatically when address + dog are both present.
    """
    user = User.query.filter(User.role == 'client', User.id == client_id).first_or_404()
    client = Client.query.filter_by(user_id=user.id).first()
    dog_owner = DogOwner.query.filter_by(user_id=user.id, role='primary').first()
    dog = Dog.query.get(dog_owner.dog_id) if dog_owner else None

    form = ClientCreateForm()

    if form.validate_on_submit():
        try:
            user.firstname = form.firstname.data.strip().title()
            user.lastname = form.lastname.data.strip().title()

            if form.notify_email.data and form.notify_whatsapp.data:
                user.notification_preference = 'both'
            elif form.notify_whatsapp.data:
                user.notification_preference = 'whatsapp'
            else:
                user.notification_preference = 'email'
            user.phone = form.phone.data.strip() if form.phone.data else None

            if not client:
                client = Client(user_id=user.id)
                db.session.add(client)

            has_address = bool(form.address_line_1.data and form.address_line_1.data.strip())
            if has_address:
                client.street_address = form.address_line_1.data.strip()
                if form.address_line_2.data and form.address_line_2.data.strip():
                    client.street_address += '\n' + form.address_line_2.data.strip()
                if form.address_line_3.data and form.address_line_3.data.strip():
                    client.street_address += '\n' + form.address_line_3.data.strip()
                client.postal_code = form.postcode.data.strip() if form.postcode.data else None
            else:
                client.street_address = None
                client.postal_code = None
            client.pickup_instructions = form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None

            has_dog = bool(form.dog_name.data and form.dog_gender.data and form.dog_dob.data)
            if has_dog:
                if dog:
                    dog.name = form.dog_name.data.strip()
                    dog.gender = form.dog_gender.data
                    dog.breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
                    dog.allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""
                    dog.date_of_birth = form.dog_dob.data
                else:
                    new_dog = Dog(
                        name=form.dog_name.data.strip(),
                        gender=form.dog_gender.data,
                        breed=form.dog_breed.data.strip() if form.dog_breed.data else "",
                        allergies=form.dog_allergies.data.strip() if form.dog_allergies.data else "",
                        date_of_birth=form.dog_dob.data,
                    )
                    db.session.add(new_dog)
                    db.session.flush()
                    db.session.add(DogOwner(dog_id=new_dog.id, user_id=user.id, role='primary'))

            # Auto-complete onboarding when we now have the full picture
            if has_address and has_dog and not client.onboarding_completed:
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)

            db.session.commit()
            flash("Client details updated successfully.", "success")
            return redirect(url_for('admin.client_detail', client_id=user.id))

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error editing client {client_id}: {e}")
            flash("An error occurred while saving changes.", "error")

    elif request.method == 'GET':
        form.firstname.data = user.firstname
        form.lastname.data = user.lastname
        form.phone.data = user.phone
        form.notify_email.data = (user.notification_preference or 'email') in ('email', 'both')
        form.notify_whatsapp.data = (user.notification_preference or '') in ('whatsapp', 'both')

        if client:
            if client.street_address:
                lines = client.street_address.split('\n')
                form.address_line_1.data = lines[0] if len(lines) > 0 else ''
                form.address_line_2.data = lines[1] if len(lines) > 1 else ''
                form.address_line_3.data = lines[2] if len(lines) > 2 else ''
            form.postcode.data = client.postal_code
            form.pickup_instructions.data = client.pickup_instructions
            form.maps_url.data = client.maps_url

        if dog:
            form.dog_name.data = dog.name
            form.dog_gender.data = dog.gender
            form.dog_breed.data = dog.breed
            form.dog_dob.data = dog.date_of_birth
            form.dog_allergies.data = dog.allergies

    return render_template(
        "admin_client_form.html",
        form=form,
        title=f"Edit {user.full_name}",
        is_edit=True,
        client_user=user,
    )


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


@admin_bp.route("/clients/<int:client_id>/pickup-details", methods=["POST"])
@login_required
@admin_required
def update_client_pickup_details(client_id):
    """Save pickup_instructions and maps_url for a client (admin only)."""
    user = User.query.filter(User.role == 'client', User.id == client_id).first()
    if not user:
        return jsonify(success=False, message="Client not found"), 404

    client = Client.query.filter_by(user_id=user.id).first()
    if not client:
        return jsonify(success=False, message="Client record not found"), 404

    data = request.get_json(silent=True) or {}
    pickup_instructions = (data.get('pickup_instructions') or '').strip() or None
    maps_url = (data.get('maps_url') or '').strip() or None

    if maps_url and len(maps_url) > 2048:
        return jsonify(success=False, message="Maps URL too long"), 400
    if pickup_instructions and len(pickup_instructions) > 500:
        return jsonify(success=False, message="Instructions too long (max 500 chars)"), 400

    client.pickup_instructions = pickup_instructions
    client.maps_url = maps_url
    db.session.commit()
    return jsonify(success=True)


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
