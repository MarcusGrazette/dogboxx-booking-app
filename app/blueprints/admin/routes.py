"""
Admin routes.

This module defines routes for admin functionality, including dashboard, booking
management, and user management.
"""

from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models import User, Booking, Walker, Dog, Client, WalkerSchedule, DogOwner, WalkerUnavailability, WalkerAdHocAvailability, ServiceType, Notification
from app import db
from app.capacity import get_max_per_walker, get_walker_slot_count, get_drop_in_capacity
from app.utils.db_error_handler import handle_db_errors
from app.forms import ClientCreateForm, WalkerCreateForm, WalkerScheduleForm
from app.utils.uploads import process_dog_photo
from app.utils.invoicing import invoice_for_client as _invoice_for_client
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

    adhoc_entries = WalkerAdHocAvailability.query.filter(
        WalkerAdHocAvailability.date >= today,
        WalkerAdHocAvailability.date <= chart_end,
    ).all()
    # Build: {walker_id: {date: set of ad hoc slots}}
    adhoc_map = {}
    for a in adhoc_entries:
        adhoc_map.setdefault(a.walker_id, {}).setdefault(a.date, set()).add(a.slot)

    # Build grid: list of {walker, days: [{date, slots: ['Morning','Afternoon']}]}
    walker_grid = []
    for walker in all_walkers:
        days = []
        for d in weekdays:
            dow = d.weekday()
            scheduled = schedule_map.get(walker.id, {}).get(dow, set())
            adhoc = adhoc_map.get(walker.id, {}).get(d, set())
            blocked = unavail_map.get(walker.id, {}).get(d, set())
            available = (scheduled | adhoc) - blocked
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

    Each dict: {date, revenue, walks, drop_ins, doubles, price_per_walk,
                price_per_drop_in, discount}

    Logic per day:
      - Group walks: count confirmed bookings by (dog_id, slot); discount for
        dogs with BOTH Morning + Afternoon on the same day
      - Drop-ins: counted separately, priced at price_per_drop_in (no double discount)
      - revenue = (walks * price_per_walk - doubles * discount) + (drop_ins * price_per_drop_in)
    Uses the PricingConfig with the highest effective_from <= that day.
    """
    from datetime import timedelta
    from app.models import PricingConfig, Booking, ServiceType

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
        return None

    # Group walk bookings: (date, dog_id, slot)
    walk_rows = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date >= start,
            Booking.date <= end,
            Booking.status == 'confirmed',
            Booking.slot.in_(['Morning', 'Afternoon']),
            ServiceType.slug == 'group-walk',
        )
        .with_entities(Booking.date, Booking.dog_id, Booking.slot)
        .all()
    )

    # Drop-in bookings: (date,)
    drop_in_rows = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date >= start,
            Booking.date <= end,
            Booking.status == 'confirmed',
            ServiceType.slug == 'drop-in',
        )
        .with_entities(Booking.date)
        .all()
    )

    # Build lookups
    day_dog_slots = {}
    for r in walk_rows:
        day_dog_slots.setdefault(r.date, {}).setdefault(r.dog_id, set()).add(r.slot)

    day_drop_ins = {}
    for r in drop_in_rows:
        day_drop_ins[r.date] = day_drop_ins.get(r.date, 0) + 1

    results = []
    d = start
    while d <= end:
        dog_slots  = day_dog_slots.get(d, {})
        walks      = sum(len(slots) for slots in dog_slots.values())
        doubles    = sum(1 for slots in dog_slots.values()
                         if 'Morning' in slots and 'Afternoon' in slots)
        drop_ins   = day_drop_ins.get(d, 0)
        cfg = config_for(d)
        if cfg:
            price          = float(cfg.price_per_walk)
            drop_in_price  = float(cfg.price_per_drop_in)
            discount       = float(cfg.double_slot_discount)
            revenue        = round(
                walks * price - doubles * discount + drop_ins * drop_in_price, 2
            )
        else:
            price = drop_in_price = discount = revenue = 0.0
        results.append({
            'date':              d,
            'revenue':           revenue,
            'walks':             walks,
            'drop_ins':          drop_ins,
            'doubles':           doubles,
            'price_per_walk':    price,
            'price_per_drop_in': drop_in_price,
            'discount':          discount,
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
        price            = float(request.form['price_per_walk'])
        discount         = float(request.form['double_slot_discount'])
        weekly_disc      = float(request.form.get('weekly_discount', 0))
        drop_in_price    = float(request.form.get('price_per_drop_in', 5))
        eff_from         = date.fromisoformat(request.form['effective_from'])
    except (KeyError, ValueError) as e:
        flash(f"Invalid pricing data: {e}", "danger")
        return redirect(url_for('admin.revenue'))

    # Check for duplicate effective_from
    existing = PricingConfig.query.filter_by(effective_from=eff_from).first()
    if existing:
        existing.price_per_walk       = price
        existing.double_slot_discount = discount
        existing.weekly_discount      = weekly_disc
        existing.price_per_drop_in    = drop_in_price
        flash(f"Pricing for {eff_from} updated.", "success")
    else:
        db.session.add(PricingConfig(
            price_per_walk=price,
            double_slot_discount=discount,
            weekly_discount=weekly_disc,
            price_per_drop_in=drop_in_price,
            effective_from=eff_from,
        ))
        flash(f"New pricing tier effective from {eff_from} added.", "success")

    db.session.commit()
    return redirect(url_for('admin.revenue'))


@admin_bp.route("/board")
@login_required
@admin_required
def board():
    """Group walk assignment board — click-to-assign + drag-to-reorder."""
    return render_template("admin_board.html")


@admin_bp.route("/drop-in-board")
@login_required
@admin_required
def drop_in_board():
    """Drop-in assignment board."""
    return render_template("admin_drop_in_board.html")


@admin_bp.route("/api/pending-counts")
@login_required
@admin_required
def pending_counts():
    """Return pending booking counts for sidebar badge updates."""
    PENDING = ('requested', 'waitlisted')
    gw = ServiceType.query.filter_by(slug='group-walk').first()
    di = ServiceType.query.filter_by(slug='drop-in').first()
    group_walks = (
        Booking.query
        .filter(Booking.status.in_(PENDING), Booking.service_type_id == gw.id)
        .count()
    ) if gw else 0
    drop_ins = (
        Booking.query
        .filter(Booking.status.in_(PENDING), Booking.service_type_id == di.id)
        .count()
    ) if di else 0
    return jsonify(group_walks=group_walks, drop_ins=drop_ins)


@admin_bp.route("/api/drop-in-board-data/<date_str>")
@login_required
@admin_required
def drop_in_board_data(date_str):
    """JSON board data for drop-in bookings on a given date."""
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    drop_in_service = ServiceType.query.filter_by(slug='drop-in').first()
    if not drop_in_service:
        return jsonify(success=False, message="Drop-in service type not configured"), 500

    all_bookings = (
        Booking.query
        .options(
            joinedload(Booking.dog),
            joinedload(Booking.walker).joinedload(Walker.user),
            joinedload(Booking.user),
        )
        .filter(
            Booking.date == selected_date,
            Booking.status != 'cancelled',
            Booking.service_type_id == drop_in_service.id,
        )
        .all()
    )

    # Only walkers with does_drop_ins=True, scheduled for this day/slot or with ad hoc availability
    day_of_week = selected_date.weekday()
    schedules = (
        WalkerSchedule.query
        .join(Walker)
        .filter(
            WalkerSchedule.day_of_week == day_of_week,
            WalkerSchedule.active == True,
            Walker.does_drop_ins == True,
        )
        .all()
    )
    walker_sched_slots = {}
    for s in schedules:
        walker_sched_slots.setdefault(s.walker_id, set()).add(s.slot)

    # Ad hoc availability for drop-in walkers
    adhoc_entries = (
        WalkerAdHocAvailability.query
        .join(Walker)
        .filter(
            WalkerAdHocAvailability.date == selected_date,
            Walker.does_drop_ins == True,
        )
        .all()
    )
    walker_adhoc_slots = {}
    for a in adhoc_entries:
        walker_adhoc_slots.setdefault(a.walker_id, set()).add(a.slot)

    unavailabilities = WalkerUnavailability.query.filter_by(date=selected_date).all()
    walker_unavail_slots = {}
    for u in unavailabilities:
        walker_unavail_slots.setdefault(u.walker_id, set()).add(u.slot)

    all_board_walker_ids = set(walker_sched_slots.keys()) | set(walker_adhoc_slots.keys())
    walkers = (
        Walker.query.options(joinedload(Walker.user))
        .filter(Walker.id.in_(all_board_walker_ids))
        .all()
    ) if all_board_walker_ids else []

    def booking_dict(b):
        return {
            'id': b.id,
            'dog_name': b.dog.name if b.dog else 'Unknown',
            'dog_pic': b.dog.pic if b.dog and b.dog.pic else None,
            'owner_name': b.dog.owners_display if b.dog else (b.user.full_name if b.user else ''),
            'slot': b.slot,
            'status': b.status,
            'pickup_order': b.pickup_order,
            'walker_id': b.walker_id,
            'has_notes': bool(b.dog and b.dog.pickup_instructions),
        }

    pending  = [booking_dict(b) for b in all_bookings if b.status in ('requested', 'waitlisted')]
    assigned = [booking_dict(b) for b in all_bookings if b.walker_id and b.status == 'confirmed']

    slot_order = lambda s: 0 if s == 'Morning' else 1
    walkers_data = [
        {
            'id': w.id,
            'name': w.user.firstname if w.user else 'Walker',
            'available_slots': sorted(
                (walker_sched_slots.get(w.id, set()) | walker_adhoc_slots.get(w.id, set()))
                - walker_unavail_slots.get(w.id, set()),
                key=slot_order
            ),
            'unavailable_slots': sorted(walker_unavail_slots.get(w.id, []), key=slot_order),
        }
        for w in walkers
    ]

    max_capacity = get_max_per_walker('drop-in')

    return jsonify(
        success=True,
        date=date_str,
        pending=pending,
        assigned=assigned,
        walkers=walkers_data,
        max_capacity=max_capacity,
    )


@admin_bp.route("/api/board-data/<date_str>")
@login_required
@admin_required
def board_data(date_str):
    """JSON board data for a given date — pending bookings, walkers, assignments."""
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    group_walk_service = ServiceType.query.filter_by(slug='group-walk').first()
    all_bookings = (
        Booking.query
        .options(
            joinedload(Booking.dog),
            joinedload(Booking.walker).joinedload(Walker.user),
            joinedload(Booking.user),
        )
        .filter(
            Booking.date == selected_date,
            Booking.status != 'cancelled',
            Booking.service_type_id == group_walk_service.id if group_walk_service else True,
        )
        .all()
    )

    day_of_week = selected_date.weekday()
    schedules = WalkerSchedule.query.filter_by(day_of_week=day_of_week, active=True).all()
    walker_sched_slots = {}   # default-schedule slots regardless of unavailability
    for s in schedules:
        walker_sched_slots.setdefault(s.walker_id, set()).add(s.slot)

    # Ad hoc availability for this specific date
    adhoc_entries = WalkerAdHocAvailability.query.filter_by(date=selected_date).all()
    walker_adhoc_slots = {}
    for a in adhoc_entries:
        walker_adhoc_slots.setdefault(a.walker_id, set()).add(a.slot)

    # Track which slots each walker has marked unavailable
    unavailabilities = WalkerUnavailability.query.filter_by(date=selected_date).all()
    walker_unavail_slots = {}
    for u in unavailabilities:
        walker_unavail_slots.setdefault(u.walker_id, set()).add(u.slot)

    # Union of scheduled + ad hoc walker IDs — all appear on the board
    all_board_walker_ids = set(walker_sched_slots.keys()) | set(walker_adhoc_slots.keys())
    walkers = (
        Walker.query.options(joinedload(Walker.user))
        .filter(Walker.id.in_(all_board_walker_ids))
        .all()
    ) if all_board_walker_ids else []

    # Dogs that have active bookings in BOTH Morning and Afternoon today — used for the
    # double-walk icon on board cards (whether booked via "both walks" or manually).
    from collections import defaultdict
    _dog_slots = defaultdict(set)
    for b in all_bookings:
        if b.status not in ('cancelled', 'rejected'):
            _dog_slots[b.dog_id].add(b.slot)
    both_slots_dog_ids = {
        dog_id for dog_id, slots in _dog_slots.items()
        if 'Morning' in slots and 'Afternoon' in slots
    }

    def booking_dict(b):
        d = {
            'id': b.id,
            'dog_name': b.dog.name if b.dog else 'Unknown',
            'dog_pic': b.dog.pic if b.dog and b.dog.pic else None,
            'owner_name': b.dog.owners_display if b.dog else (b.user.full_name if b.user else ''),
            'slot': b.slot,
            'status': b.status,
            'pickup_order': b.pickup_order,
            'walker_id': b.walker_id,
            'has_both_slots': b.dog_id in both_slots_dog_ids,
            'has_notes': bool(b.dog and b.dog.pickup_instructions),
        }
        return d

    pending   = [booking_dict(b) for b in all_bookings if b.status in ('requested', 'waitlisted')]
    assigned  = [booking_dict(b) for b in all_bookings if b.walker_id and b.status == 'confirmed']

    slot_order = lambda s: 0 if s == 'Morning' else 1
    walkers_data = [
        {
            'id': w.id,
            'name': w.user.firstname if w.user else 'Walker',
            'available_slots': sorted(
                (walker_sched_slots.get(w.id, set()) | walker_adhoc_slots.get(w.id, set()))
                - walker_unavail_slots.get(w.id, set()),
                key=slot_order
            ),
            'unavailable_slots': sorted(walker_unavail_slots.get(w.id, []), key=slot_order),
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

        booking = db.session.get(Booking, booking_id)
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

        # Check walker capacity for the given slot and date (scoped to same service type)
        if slot:
            service_slug = booking.service_type.slug if booking.service_type else 'group-walk'
            max_capacity = get_max_per_walker(service_slug)
            same_slot_bookings = Booking.query.join(ServiceType).filter(
                Booking.walker_id == walker.id,
                Booking.date == booking.date,
                Booking.slot == slot,
                Booking.status != 'cancelled',
                Booking.id != booking.id,
                ServiceType.slug == service_slug,
            ).count()

            if same_slot_bookings >= max_capacity:
                return jsonify(success=False, message=f"Walker already has maximum bookings ({max_capacity}) for {slot} slot"), 400

        # Update walker assignment and slot
        booking.walker_id = walker.id
        booking.status = 'confirmed'
        if slot:
            booking.slot = slot

        # Notify client + walker — label differs by service type
        date_str_fmt = booking.date.strftime('%a %-d %b')
        dog_name = booking.dog.name if booking.dog else 'your dog'
        service_label = 'drop-in visit' if (booking.service_type and booking.service_type.slug == 'drop-in') else 'walk'

        create_notification(
            recipient_id=booking.user_id,
            notification_type='booking_confirmed',
            title=f"{dog_name}'s {service_label} on {date_str_fmt} has been confirmed",
            body=booking.slot,
            link=f'/bookings/{booking.id}',
            sender_id=current_user.id,
        )

        create_notification(
            recipient_id=walker.user_id,
            notification_type='walker_assigned',
            title=f'You have been assigned a {service_label} on {date_str_fmt}',
            body=f'{dog_name} — {booking.slot}',
            link=f'/walker/pickups?date={booking.date.isoformat()}',
            sender_id=current_user.id,
        )

        # Update pickup order for all bookings in this walker's slot
        pickup_order = data.get("pickup_order")  # list of booking IDs in order
        if pickup_order and isinstance(pickup_order, list):
            for idx, bid in enumerate(pickup_order, start=1):
                b = db.session.get(Booking, int(bid))
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
            b = db.session.get(Booking, int(bid))
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
    """Show client detail with dog info, shared access, and notification audit trail (admin only)"""
    user = User.query.filter(User.role == 'client', User.id == client_id).first_or_404()

    # Dogs where this user is the primary owner
    primary_ownerships = DogOwner.query.filter_by(user_id=user.id, role='primary').all()
    primary_dogs = []
    for ownership in primary_ownerships:
        dog = db.session.get(Dog, ownership.dog_id)
        if not dog:
            continue
        secondary_ownerships = DogOwner.query.filter_by(dog_id=dog.id, role='secondary').all()
        secondary_users = [db.session.get(User, so.user_id) for so in secondary_ownerships]
        secondary_users = [u for u in secondary_users if u]  # filter None
        primary_dogs.append({'dog': dog, 'secondary_owners': secondary_users})

    # Dogs where this user is a secondary owner (joined from another account)
    secondary_ownerships = DogOwner.query.filter_by(user_id=user.id, role='secondary').all()
    secondary_dogs = []
    for ownership in secondary_ownerships:
        dog = db.session.get(Dog, ownership.dog_id)
        if not dog:
            continue
        primary_o = DogOwner.query.filter_by(dog_id=dog.id, role='primary').first()
        primary_user = db.session.get(User, primary_o.user_id) if primary_o else None
        secondary_dogs.append({'dog': dog, 'primary_owner': primary_user})

    # Clients available to join — exclude self and anyone already linked
    already_linked_ids = {user.id}
    for pd in primary_dogs:
        for so in pd['secondary_owners']:
            already_linked_ids.add(so.id)
    for sd in secondary_dogs:
        if sd['primary_owner']:
            already_linked_ids.add(sd['primary_owner'].id)
    available_clients = (
        User.query
        .filter(User.role == 'client', User.active == True)
        .filter(~User.id.in_(already_linked_ids))
        .order_by(User.lastname, User.firstname)
        .all()
    )

    # Backward-compat: keep `dog` pointing at first primary dog for old template sections
    dog = primary_dogs[0]['dog'] if primary_dogs else None

    notifications = (
        Notification.query
        .filter_by(recipient_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    from app.forms import AddDogForm
    return render_template(
        "admin_client_detail.html",
        client=user,
        dog=dog,
        primary_dogs=primary_dogs,
        secondary_dogs=secondary_dogs,
        available_clients=available_clients,
        notifications=notifications,
        add_dog_form=AddDogForm(),
        add_dog_modal_open=False,
    )


@admin_bp.route("/clients/<int:client_id>/join", methods=["POST"])
@login_required
@admin_required
def join_dog_access(client_id):
    """Grant a secondary client shared access to the primary client's dog.

    Expects JSON: { "dog_id": int, "secondary_user_id": int }
    The secondary user gains read/book/cancel access to the dog but is not
    the primary owner — they cannot modify the dog's profile.
    """
    primary_user = User.query.filter(User.role == 'client', User.id == client_id).first_or_404()
    data = request.get_json(silent=True) or {}
    dog_id = data.get('dog_id')
    secondary_user_id = data.get('secondary_user_id')

    if not dog_id or not secondary_user_id:
        return jsonify(success=False, message="Missing dog_id or secondary_user_id"), 400

    # Verify dog belongs to this primary client
    ownership = DogOwner.query.filter_by(dog_id=dog_id, user_id=primary_user.id, role='primary').first()
    if not ownership:
        return jsonify(success=False, message="Dog not found for this client"), 404

    secondary_user = User.query.filter(User.role == 'client', User.id == secondary_user_id).first()
    if not secondary_user:
        return jsonify(success=False, message="Secondary client not found"), 404

    if secondary_user_id == client_id:
        return jsonify(success=False, message="Cannot join an account to itself"), 400

    existing = DogOwner.query.filter_by(dog_id=dog_id, user_id=secondary_user_id).first()
    if existing:
        return jsonify(success=False, message=f"{secondary_user.full_name} already has access to this dog"), 409

    try:
        db.session.add(DogOwner(dog_id=dog_id, user_id=secondary_user_id, role='secondary'))

        # If the secondary user hasn't completed onboarding yet (e.g. admin created
        # their account without a dog), mark it complete — they'll use the shared dog
        # and don't need to go through the onboarding flow.
        secondary_client = Client.query.filter_by(user_id=secondary_user_id).first()
        if secondary_client and not secondary_client.onboarding_completed:
            secondary_client.onboarding_completed = True
            secondary_client.onboarding_completed_at = datetime.now(timezone.utc)

        db.session.commit()
        logging.info(
            f"Admin {current_user.id} granted {secondary_user.email} secondary access "
            f"to dog {dog_id} (primary: {primary_user.email})"
        )
        return jsonify(
            success=True,
            message=f"{secondary_user.full_name} now has access to {ownership.dog.name}",
            secondary_user={'id': secondary_user.id, 'full_name': secondary_user.full_name, 'email': secondary_user.email},
        )
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error joining accounts: {e}")
        return jsonify(success=False, message="An error occurred"), 500


@admin_bp.route("/clients/<int:client_id>/revoke-access", methods=["POST"])
@login_required
@admin_required
def revoke_dog_access(client_id):
    """Remove a secondary client's shared access to a dog.

    Expects JSON: { "dog_id": int, "secondary_user_id": int }
    Can be called from either the primary or secondary client's detail page.
    Will not remove primary ownership.
    """
    User.query.filter(User.role == 'client', User.id == client_id).first_or_404()
    data = request.get_json(silent=True) or {}
    dog_id = data.get('dog_id')
    secondary_user_id = data.get('secondary_user_id')

    if not dog_id or not secondary_user_id:
        return jsonify(success=False, message="Missing dog_id or secondary_user_id"), 400

    record = DogOwner.query.filter_by(dog_id=dog_id, user_id=secondary_user_id, role='secondary').first()
    if not record:
        return jsonify(success=False, message="No secondary access record found"), 404

    secondary_user = db.session.get(User, secondary_user_id)
    dog = db.session.get(Dog, dog_id)

    try:
        db.session.delete(record)
        db.session.commit()
        logging.info(
            f"Admin {current_user.id} revoked secondary access for user {secondary_user_id} "
            f"from dog {dog_id}"
        )
        # Notify the secondary user their access was removed
        if secondary_user and dog:
            from app.utils.notifications import create_notification
            create_notification(
                recipient_id=secondary_user.id,
                notification_type='system',
                title=f"Your access to {dog.name} has been removed",
                body="Contact Dogboxx if you think this is a mistake.",
                link='/profile',
            )
            db.session.commit()
        return jsonify(
            success=True,
            message=f"Access revoked for {secondary_user.full_name if secondary_user else secondary_user_id}",
        )
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error revoking dog access: {e}")
        return jsonify(success=False, message="An error occurred"), 500


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
            user.notification_preference = 'email'
            user.email_marketing = bool(form.notify_email.data)
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
                    whatsapp_group_url=(form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None,
                    pickup_instructions=form.pickup_instructions.data.strip() if form.pickup_instructions.data else None,
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

            flash(f"Client account created for {user.firstname} {user.lastname}.", "success")
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
    dog = db.session.get(Dog, dog_owner.dog_id) if dog_owner else None

    form = ClientCreateForm()

    # In edit mode the email field is rendered as a disabled (non-submitted) input,
    # so inject the existing email before validation to satisfy DataRequired.
    if request.method == 'POST':
        form.email.data = user.email

    if form.validate_on_submit():
        try:
            user.firstname = form.firstname.data.strip().title()
            user.lastname = form.lastname.data.strip().title()

            user.notification_preference = 'email'
            user.email_marketing = bool(form.notify_email.data)
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
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None

            has_dog = bool(form.dog_name.data and form.dog_gender.data and form.dog_dob.data)
            pickup_notes = form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
            if has_dog:
                if dog:
                    dog.name = form.dog_name.data.strip()
                    dog.gender = form.dog_gender.data
                    dog.breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
                    dog.allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""
                    dog.date_of_birth = form.dog_dob.data
                    dog.whatsapp_group_url = (form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None
                    dog.pickup_instructions = pickup_notes
                else:
                    new_dog = Dog(
                        name=form.dog_name.data.strip(),
                        gender=form.dog_gender.data,
                        breed=form.dog_breed.data.strip() if form.dog_breed.data else "",
                        allergies=form.dog_allergies.data.strip() if form.dog_allergies.data else "",
                        date_of_birth=form.dog_dob.data,
                        whatsapp_group_url=(form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None,
                        pickup_instructions=pickup_notes,
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
        form.notify_email.data = user.email_marketing

        if client:
            if client.street_address:
                lines = client.street_address.split('\n')
                form.address_line_1.data = lines[0] if len(lines) > 0 else ''
                form.address_line_2.data = lines[1] if len(lines) > 1 else ''
                form.address_line_3.data = lines[2] if len(lines) > 2 else ''
            form.postcode.data = client.postal_code
            form.maps_url.data = client.maps_url

        if dog:
            form.pickup_instructions.data = dog.pickup_instructions

        if dog:
            form.dog_name.data = dog.name
            form.dog_gender.data = dog.gender
            form.dog_breed.data = dog.breed
            form.dog_dob.data = dog.date_of_birth
            form.dog_allergies.data = dog.allergies
            form.dog_whatsapp_group_url.data = dog.whatsapp_group_url

    return render_template(
        "admin_client_form.html",
        form=form,
        title=f"Edit {user.full_name}",
        is_edit=True,
        client_user=user,
    )


@admin_bp.route("/clients/<int:client_id>/add-dog", methods=["POST"])
@login_required
@admin_required
def add_dog(client_id):
    """Add a second (or subsequent) primary dog to an existing client."""
    from app.forms import AddDogForm
    user = User.query.filter(User.role == 'client', User.id == client_id).first_or_404()

    form = AddDogForm()
    if form.validate_on_submit():
        try:
            new_dog = Dog(
                name=form.dog_name.data.strip(),
                gender=form.dog_gender.data,
                breed=form.dog_breed.data.strip() if form.dog_breed.data else "",
                date_of_birth=form.dog_dob.data,
                allergies=form.dog_allergies.data.strip() if form.dog_allergies.data else "",
                pickup_instructions=form.pickup_instructions.data.strip() if form.pickup_instructions.data else None,
                whatsapp_group_url=(form.dog_whatsapp_group_url.data.strip() or None) if form.dog_whatsapp_group_url.data else None,
            )
            db.session.add(new_dog)
            db.session.flush()
            db.session.add(DogOwner(dog_id=new_dog.id, user_id=user.id, role='primary'))
            db.session.commit()
            flash(f"{new_dog.name} added successfully.", "success")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error adding dog for client {client_id}: {e}")
            flash("An error occurred while adding the dog.", "error")
        return redirect(url_for('admin.client_detail', client_id=client_id))

    # Validation failed — re-render the detail page with the modal open
    # Re-build everything client_detail needs
    primary_ownerships = DogOwner.query.filter_by(user_id=user.id, role='primary').all()
    primary_dogs = []
    for ownership in primary_ownerships:
        dog = db.session.get(Dog, ownership.dog_id)
        if not dog:
            continue
        secondary_ownerships = DogOwner.query.filter_by(dog_id=dog.id, role='secondary').all()
        secondary_users = [db.session.get(User, so.user_id) for so in secondary_ownerships]
        secondary_users = [u for u in secondary_users if u]
        primary_dogs.append({'dog': dog, 'secondary_owners': secondary_users})

    secondary_ownerships = DogOwner.query.filter_by(user_id=user.id, role='secondary').all()
    secondary_dogs = []
    for ownership in secondary_ownerships:
        dog = db.session.get(Dog, ownership.dog_id)
        if not dog:
            continue
        primary_o = DogOwner.query.filter_by(dog_id=dog.id, role='primary').first()
        primary_user = db.session.get(User, primary_o.user_id) if primary_o else None
        secondary_dogs.append({'dog': dog, 'primary_owner': primary_user})

    already_linked_ids = {user.id}
    for pd in primary_dogs:
        for so in pd['secondary_owners']:
            already_linked_ids.add(so.id)
    for sd in secondary_dogs:
        if sd['primary_owner']:
            already_linked_ids.add(sd['primary_owner'].id)
    available_clients = (
        User.query
        .filter(User.role == 'client', User.active == True)
        .filter(~User.id.in_(already_linked_ids))
        .order_by(User.lastname, User.firstname)
        .all()
    )

    dog = primary_dogs[0]['dog'] if primary_dogs else None
    notifications = (
        Notification.query
        .filter_by(recipient_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "admin_client_detail.html",
        client=user,
        dog=dog,
        primary_dogs=primary_dogs,
        secondary_dogs=secondary_dogs,
        available_clients=available_clients,
        notifications=notifications,
        add_dog_form=form,
        add_dog_modal_open=True,
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

        if user.id == current_user.id:
            return jsonify(success=False, message="You cannot deactivate your own account"), 400

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

    # Pickup notes now live on the dog, not the client
    from app.models import DogOwner
    dog_owner = DogOwner.query.filter_by(user_id=user.id, role='primary').first()
    dog = db.session.get(Dog, dog_owner.dog_id) if dog_owner else None
    if not dog:
        return jsonify(success=False, message="No dog record found — add a dog first before saving pickup notes"), 404
    dog.pickup_instructions = pickup_instructions
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


@admin_bp.route("/walkers/<int:walker_user_id>/toggle-admin", methods=["POST"])
@login_required
@admin_required
def toggle_walker_admin(walker_user_id):
    """Promote or demote a walker's admin access. Super-admin only."""
    if not current_user.is_super_admin:
        return jsonify(success=False, message="Only the business owner can change admin access."), 403

    if walker_user_id == current_user.id:
        return jsonify(success=False, message="You cannot change your own admin access."), 400

    target = User.query.filter_by(id=walker_user_id, role='walker').first_or_404()

    if target.is_super_admin:
        return jsonify(success=False, message="Cannot change admin access for the business owner."), 400

    target.is_admin = not target.is_admin
    db.session.commit()

    return jsonify(success=True, is_admin=target.is_admin)


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
            
            logging.info(f"Admin {current_user.id} created walker account for {user.email}")
            flash(f"Walker account created for {user.firstname} {user.lastname}.", "success")
            
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

        if user.id == current_user.id:
            return jsonify(success=False, message="You cannot deactivate your own account"), 400

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

    # Admins can edit any walker's schedule; walkers can only edit their own
    if not current_user.is_admin:
        own_walker = Walker.query.filter_by(user_id=current_user.id).first()
        if not own_walker or own_walker.id != walker_id:
            return jsonify(success=False, message="Forbidden"), 403
    
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


# ─── Walker schedule overrides (ad hoc available + unavailability) ───────────

@admin_bp.route("/walkers/overrides")
@login_required
@admin_required
def walker_overrides():
    """Admin page: manage ad hoc availability and unavailability for any walker."""
    from datetime import date

    active_walkers = (
        Walker.query
        .join(Walker.user)
        .filter(User.active == True, User.role == 'walker')
        .order_by(User.lastname, User.firstname)
        .all()
    )

    today = date.today()

    selected_walker = None
    adhoc_list = []
    unavail_list = []

    walker_id_str = request.args.get('walker_id')
    if walker_id_str:
        try:
            wid = int(walker_id_str)
            selected_walker = next((w for w in active_walkers if w.id == wid), None)
        except (ValueError, TypeError):
            pass

    if not selected_walker and active_walkers:
        selected_walker = active_walkers[0]

    if selected_walker:
        adhoc_list = (
            WalkerAdHocAvailability.query
            .filter(
                WalkerAdHocAvailability.walker_id == selected_walker.id,
                WalkerAdHocAvailability.date >= today,
            )
            .order_by(WalkerAdHocAvailability.date, WalkerAdHocAvailability.slot)
            .all()
        )
        unavail_list = (
            WalkerUnavailability.query
            .filter(
                WalkerUnavailability.walker_id == selected_walker.id,
                WalkerUnavailability.date >= today,
            )
            .order_by(WalkerUnavailability.date, WalkerUnavailability.slot)
            .all()
        )

    return render_template(
        'admin_walker_overrides.html',
        active_walkers=active_walkers,
        selected_walker=selected_walker,
        adhoc_list=adhoc_list,
        unavail_list=unavail_list,
        today=today,
    )


@admin_bp.route("/walkers/<int:walker_id>/adhoc", methods=["POST"])
@login_required
@admin_required
def admin_add_adhoc(walker_id):
    """Admin: add an ad hoc available slot for any walker."""
    from datetime import date

    walker = Walker.query.get_or_404(walker_id)

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    date_str = data.get('date')
    slot = data.get('slot')

    if not date_str or slot not in ('Morning', 'Afternoon'):
        return jsonify(success=False, message="Date and valid slot are required"), 400

    try:
        adhoc_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date format (use YYYY-MM-DD)"), 400

    # Don't add if already in default schedule (redundant)
    day_of_week = adhoc_date.weekday()
    if WalkerSchedule.query.filter_by(walker_id=walker.id, day_of_week=day_of_week, slot=slot, active=True).first():
        return jsonify(success=False, message=f"{walker.user.full_name} is already scheduled for {slot} on {adhoc_date.strftime('%A')}s"), 400

    if WalkerAdHocAvailability.query.filter_by(walker_id=walker.id, date=adhoc_date, slot=slot).first():
        return jsonify(success=False, message="Already marked as available for this date/slot"), 400

    adhoc = WalkerAdHocAvailability(walker_id=walker.id, date=adhoc_date, slot=slot)
    db.session.add(adhoc)
    db.session.commit()

    return jsonify(success=True, adhoc=adhoc.to_dict()), 201


@admin_bp.route("/walkers/<int:walker_id>/adhoc/<int:adhoc_id>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_adhoc(walker_id, adhoc_id):
    """Admin: remove an ad hoc available slot."""
    adhoc = db.session.get(WalkerAdHocAvailability, adhoc_id)
    if not adhoc or adhoc.walker_id != walker_id:
        return jsonify(success=False, message="Not found"), 404
    db.session.delete(adhoc)
    db.session.commit()
    return jsonify(success=True)


@admin_bp.route("/walkers/<int:walker_id>/unavailability", methods=["POST"])
@login_required
@admin_required
def admin_add_unavailability(walker_id):
    """Admin: add an unavailability slot for any walker."""
    walker = Walker.query.get_or_404(walker_id)

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    date_str = data.get('date')
    slot = data.get('slot')
    reason = data.get('reason', '').strip() or None

    if not date_str or slot not in ('Morning', 'Afternoon'):
        return jsonify(success=False, message="Date and valid slot are required"), 400

    try:
        unavail_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date format (use YYYY-MM-DD)"), 400

    if WalkerUnavailability.query.filter_by(walker_id=walker.id, date=unavail_date, slot=slot).first():
        return jsonify(success=False, message="Already marked as unavailable for this date/slot"), 400

    unavail = WalkerUnavailability(walker_id=walker.id, date=unavail_date, slot=slot, reason=reason)
    db.session.add(unavail)
    db.session.commit()

    return jsonify(success=True, unavailability=unavail.to_dict()), 201


@admin_bp.route("/walkers/<int:walker_id>/unavailability/<int:unavail_id>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_unavailability(walker_id, unavail_id):
    """Admin: remove an unavailability entry."""
    unavail = db.session.get(WalkerUnavailability, unavail_id)
    if not unavail or unavail.walker_id != walker_id:
        return jsonify(success=False, message="Not found"), 404
    db.session.delete(unavail)
    db.session.commit()
    return jsonify(success=True)


# ─── Dogs ─────────────────────────────────────────────────────────────────────

@admin_bp.route("/dogs")
@login_required
@admin_required
def dogs():
    """Admin view: all dogs on the books. Filtering is client-side."""
    rows = (
        Dog.query
        .join(DogOwner, DogOwner.dog_id == Dog.id)
        .join(User, User.id == DogOwner.user_id)
        .filter(DogOwner.role == 'primary')
        .add_columns(User.id.label('owner_user_id'),
                     User.firstname.label('owner_firstname'),
                     User.lastname.label('owner_lastname'),
                     User.email.label('owner_email'))
        .order_by(Dog.name)
        .all()
    )
    dogs_data = [
        {
            'dog': row[0],
            'owner_user_id': row[1],
            'owner_name': f"{row[2]} {row[3]}",
            'owner_email': row[4],
        }
        for row in rows
    ]
    return render_template("admin_dogs.html", dogs_data=dogs_data)


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

        dog = db.session.get(Dog, dog_id)
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

        available, can_waitlist, capacity_msg = check_availability(
            default_service, booking_date, slot, admin_override=True
        )
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

        dog = db.session.get(Dog, dog_id)
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

            available, can_waitlist, _ = check_availability(default_service, d, slot, admin_override=True)
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


# ─────────────────────────────────────────────────────────────────────────────
# Invoicing
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route("/invoicing")
@login_required
@admin_required
def invoicing():
    """Monthly invoicing summary — one row per client."""
    from datetime import date
    from calendar import monthrange

    # ── Month selection ───────────────────────────────────────────────────
    today = date.today()
    month_str = request.args.get('month', f'{today.year}-{today.month:02d}')
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    month_start = date(year, month, 1)
    month_end   = date(year + (month // 12), (month % 12) + 1, 1)

    # Prev / next month helpers for navigation
    if month == 1:
        prev_month = f'{year - 1}-12'
    else:
        prev_month = f'{year}-{month - 1:02d}'
    if month == 12:
        next_month = f'{year + 1}-01'
    else:
        next_month = f'{year}-{month + 1:02d}'

    # ── Pricing configs (loaded once) ─────────────────────────────────────
    from app.models import PricingConfig
    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= month_end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    # ── Clients ───────────────────────────────────────────────────────────
    clients = (
        User.query
        .options(joinedload(User.client))
        .filter(User.role == 'client')
        .order_by(User.lastname, User.firstname)
        .all()
    )

    rows = []
    for u in clients:
        inv = _invoice_for_client(u.id, month_start, month_end, all_configs)
        if inv is None or inv['total_billable'] == 0:
            continue
        # Primary dog + secondary owners
        do = DogOwner.query.filter_by(user_id=u.id, role='primary').first()
        dog = db.session.get(Dog, do.dog_id) if do else None
        secondary_owners = []
        if dog:
            secondary_owners = [
                db.session.get(User, so.user_id)
                for so in DogOwner.query.filter_by(dog_id=dog.id, role='secondary').all()
            ]
            secondary_owners = [s for s in secondary_owners if s]
        rows.append({
            'client':           u,
            'dog':              dog,
            'secondary_owners': secondary_owners,
            **inv,
        })

    grand_total = round(sum(r['subtotal'] for r in rows), 2)

    return render_template(
        'admin_invoicing.html',
        rows=rows,
        grand_total=grand_total,
        month_start=month_start,
        prev_month=prev_month,
        next_month=next_month,
        today=today,
    )


@admin_bp.route("/invoicing/<int:client_id>")
@login_required
@admin_required
def invoicing_detail(client_id):
    """Per-client invoice detail — line items for the selected month."""
    from datetime import date
    from itertools import groupby

    client_user = User.query.filter(
        User.role == 'client', User.id == client_id
    ).first_or_404()

    today = date.today()
    month_str = request.args.get('month', f'{today.year}-{today.month:02d}')
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    month_start = date(year, month, 1)
    month_end   = date(year + (month // 12), (month % 12) + 1, 1)

    from app.models import PricingConfig
    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= month_end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    def config_for(d):
        for c in all_configs:
            if c.effective_from <= d:
                return c
        return None

    inv = _invoice_for_client(client_user.id, month_start, month_end, all_configs)
    if inv is None:
        inv = {'confirmed': [], 'late_cancels': [], 'all_billable': [],
               'total_walks': 0, 'total_drop_ins': 0, 'total_cancels': 0,
               'total_billable': 0, 'doubles': 0, 'subtotal': 0.0}

    # Build line items with unit price (drop-ins priced separately)
    line_items = []
    late_cancel_ids = {b.id for b in inv['late_cancels']}
    for b in sorted(inv['all_billable'], key=lambda x: (x.date, x.slot)):
        cfg = config_for(b.date)
        is_drop_in_booking = b.service_type and b.service_type.slug == 'drop-in'
        if cfg:
            unit_price = float(cfg.price_per_drop_in) if is_drop_in_booking else float(cfg.price_per_walk)
        else:
            unit_price = 0.0
        line_items.append({
            'booking':      b,
            'unit_price':   unit_price,
            'is_cancel':    b.id in late_cancel_ids,
        })

    # Double-slot discount line items (group walks only — drop-ins don't qualify)
    from collections import defaultdict
    date_slots = defaultdict(set)
    for b in inv['all_billable']:
        if not (b.service_type and b.service_type.slug == 'drop-in'):
            date_slots[b.date].add(b.slot)
    discount_days = sorted(
        d for d, slots in date_slots.items()
        if 'Morning' in slots and 'Afternoon' in slots
    )
    discounts = []
    for d in discount_days:
        cfg = config_for(d)
        if cfg and cfg.double_slot_discount:
            discounts.append({'date': d, 'amount': float(cfg.double_slot_discount)})

    do = DogOwner.query.filter_by(user_id=client_user.id, role='primary').first()
    dog = db.session.get(Dog, do.dog_id) if do else None

    # ── Weekly breakdown ──────────────────────────────────────────────────
    # Find all Mon-commencing weeks that overlap the month
    from datetime import timedelta
    # First Monday on or before month_start
    first_monday = month_start - timedelta(days=month_start.weekday())
    weeks = []
    weekly_discounts = []  # per-qualifying-week discount line items for the line-items section
    wk_start = first_monday
    while wk_start < month_end:
        wk_end = wk_start + timedelta(days=7)  # exclusive

        # Line items in this week
        wk_items = [li for li in line_items if wk_start <= li['booking'].date < wk_end]
        wk_discounts = [d for d in discounts if wk_start <= d['date'] < wk_end]

        wk_confirmed  = sum(1 for li in wk_items if not li['is_cancel'] and not (li['booking'].service_type and li['booking'].service_type.slug == 'drop-in'))
        wk_drop_ins   = sum(1 for li in wk_items if not li['is_cancel'] and li['booking'].service_type and li['booking'].service_type.slug == 'drop-in')
        wk_cancels    = sum(1 for li in wk_items if li['is_cancel'])
        wk_double_discount = sum(d['amount'] for d in wk_discounts)

        # Weekly discount: ≥5 confirmed group walks in the week
        wk_weekly_discount = 0.0
        if wk_confirmed >= 5:
            cfg = config_for(wk_start)
            if cfg and cfg.weekly_discount:
                wk_weekly_discount = round(float(cfg.weekly_discount) * wk_confirmed, 2)
                weekly_discounts.append({
                    'week_start':  wk_start,
                    'walk_count':  wk_confirmed,
                    'amount':      wk_weekly_discount,
                })

        wk_discount_total = round(wk_double_discount + wk_weekly_discount, 2)
        wk_gross      = sum(li['unit_price'] for li in wk_items)
        wk_subtotal   = round(wk_gross - wk_discount_total, 2)

        weeks.append({
            'commencing':          wk_start,
            'confirmed':           wk_confirmed,
            'drop_ins':            wk_drop_ins,
            'cancels':             wk_cancels,
            'double_discount':     wk_double_discount,
            'weekly_discount':     wk_weekly_discount,
            'discount_total':      wk_discount_total,
            'subtotal':            wk_subtotal,
            'has_activity':        bool(wk_items),
        })
        wk_start = wk_end

    # Prev/next month nav
    if month == 1:
        prev_month = f'{year - 1}-12'
    else:
        prev_month = f'{year}-{month - 1:02d}'
    if month == 12:
        next_month = f'{year + 1}-01'
    else:
        next_month = f'{year}-{month + 1:02d}'

    return render_template(
        'admin_invoicing_detail.html',
        client_user=client_user,
        dog=dog,
        inv=inv,
        line_items=line_items,
        discounts=discounts,
        weekly_discounts=weekly_discounts,
        weeks=weeks,
        month_start=month_start,
        prev_month=prev_month,
        next_month=next_month,
        today=today,
    )


# ── Newsletter ────────────────────────────────────────────────────────────────

@admin_bp.route("/newsletter", methods=["GET", "POST"])
@login_required
@admin_required
def newsletter():
    """Compose and send a newsletter to all active, opted-in clients."""
    from app.utils.email import send_newsletter_batch
    from flask import current_app

    # Build recipient list: active clients who have opted in
    clients = User.query.filter_by(role='client', active=True, email_marketing=True).all()

    result = None  # {'sent': int, 'failed': int} after a send

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        html_body = request.form.get("html_body", "").strip()

        if not subject or not html_body:
            flash("Subject and body are required.", "error")
        else:
            base_url = current_app.config.get("APP_BASE_URL", "").rstrip("/")
            recipients = []
            for u in clients:
                token = u.make_unsubscribe_token()
                dog_name = "your dog"
                if u.client and u.client.dogs:
                    dog_name = u.client.dogs[0].name
                recipients.append({
                    "email": u.email,
                    "firstname": u.firstname,
                    "dog_name": dog_name,
                    "unsubscribe_url": f"{base_url}/auth/unsubscribe/{token}",
                })

            result = send_newsletter_batch(
                subject=subject,
                html_template=html_body,
                recipients=recipients,
            )
            if result["failed"] == 0:
                flash(f"Newsletter sent to {result['sent']} client(s).", "success")
            else:
                flash(f"Sent {result['sent']}, failed {result['failed']}. Check logs.", "warning")

    return render_template(
        "admin_newsletter.html",
        clients=clients,
        result=result,
    )


@admin_bp.route("/newsletter/test", methods=["POST"])
@login_required
@admin_required
def newsletter_test():
    """Send a test newsletter to lydia@dogboxx.org."""
    from app.utils.email import send_newsletter_batch
    from flask import current_app

    subject = request.form.get("subject", "").strip()
    html_body = request.form.get("html_body", "").strip()

    if not subject or not html_body:
        flash("Write a subject and body before sending a test.", "error")
        return redirect(url_for('admin.newsletter'))

    base_url = current_app.config.get("APP_BASE_URL", "").rstrip("/")
    result = send_newsletter_batch(
        subject=f"[TEST] {subject}",
        html_template=html_body,
        recipients=[{
            "email": "lydia@dogboxx.org",
            "firstname": "Lydia",
            "dog_name": "Luna",
            "unsubscribe_url": f"{base_url}/auth/unsubscribe/test",
        }],
    )
    if result["sent"]:
        flash("Test email sent to lydia@dogboxx.org.", "success")
    else:
        flash("Test email failed — check logs.", "error")

    return redirect(url_for('admin.newsletter'))


# ── CSV Client Import ─────────────────────────────────────────────────────────

CSV_IMPORT_COLUMNS = [
    'firstname', 'lastname', 'email', 'phone',
    'address_line_1', 'address_line_2', 'address_line_3', 'postcode',
    'pickup_instructions',
    'dog_name', 'dog_gender', 'dog_breed', 'dog_dob',
]

def _parse_csv_row(row, row_num):
    """Validate a single CSV row. Returns (cleaned_dict, list_of_errors)."""
    import re
    errors = []

    firstname = row.get('firstname', '').strip().title()
    lastname  = row.get('lastname',  '').strip().title()
    email     = row.get('email',     '').strip().lower()
    phone     = row.get('phone',     '').strip() or None

    if not firstname:
        errors.append('First name is required')
    if not lastname:
        errors.append('Last name is required')
    if not email:
        errors.append('Email is required')
    elif not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        errors.append(f'Invalid email: {email}')

    dog_name   = row.get('dog_name',   '').strip()
    dog_gender = row.get('dog_gender', '').strip().upper()
    dog_breed  = row.get('dog_breed',  '').strip()
    dog_dob    = row.get('dog_dob',    '').strip()

    parsed_dob = None
    if dog_dob:
        from datetime import date as date_type
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                parsed_dob = datetime.strptime(dog_dob, fmt).date()
                break
            except ValueError:
                pass
        if parsed_dob is None:
            errors.append(f'Invalid dog_dob format (use YYYY-MM-DD): {dog_dob}')

    if dog_gender and dog_gender not in ('M', 'F'):
        errors.append(f'dog_gender must be M or F (got: {dog_gender})')

    has_dog = bool(dog_name)

    cleaned = {
        'row_num':             row_num,
        'firstname':           firstname,
        'lastname':            lastname,
        'email':               email,
        'phone':               phone,
        'address_line_1':      row.get('address_line_1', '').strip() or None,
        'address_line_2':      row.get('address_line_2', '').strip() or None,
        'address_line_3':      row.get('address_line_3', '').strip() or None,
        'postcode':            row.get('postcode',        '').strip() or None,
        'pickup_instructions': row.get('pickup_instructions', '').strip() or None,
        'has_dog':             has_dog,
        'dog_name':            dog_name or None,
        'dog_gender':          dog_gender or None,
        'dog_breed':           dog_breed or None,
        'dog_dob':             parsed_dob.isoformat() if parsed_dob else None,
        'errors':              errors,
    }
    return cleaned, errors


@admin_bp.route("/clients/import", methods=["GET"])
@login_required
@admin_required
def csv_import():
    """CSV client import — upload form."""
    return render_template("admin_csv_import.html")


@admin_bp.route("/clients/import/preview", methods=["POST"])
@login_required
@admin_required
def csv_import_preview():
    """Parse uploaded CSV and return a preview for confirmation."""
    import csv, io

    f = request.files.get('csv_file')
    if not f or not f.filename.lower().endswith('.csv'):
        flash("Please upload a .csv file.", "error")
        return redirect(url_for('admin.csv_import'))

    content = f.read()
    if len(content) > 500 * 1024:
        flash("File is too large — maximum size is 500 KB.", "error")
        return redirect(url_for('admin.csv_import'))

    try:
        text = content.decode('utf-8-sig')  # handle BOM from Excel
    except UnicodeDecodeError:
        flash("Could not read file — please save as UTF-8 CSV.", "error")
        return redirect(url_for('admin.csv_import'))

    reader = csv.DictReader(io.StringIO(text))

    # Normalise header names (strip whitespace, lowercase)
    if not reader.fieldnames:
        flash("CSV file appears to be empty.", "error")
        return redirect(url_for('admin.csv_import'))

    headers = [h.strip().lower() for h in reader.fieldnames]
    required = {'firstname', 'lastname', 'email'}
    missing = required - set(headers)
    if missing:
        flash(f"CSV is missing required columns: {', '.join(sorted(missing))}", "error")
        return redirect(url_for('admin.csv_import'))

    rows = []
    for i, raw_row in enumerate(reader, start=2):
        # Re-key with normalised headers
        normalised = {k.strip().lower(): v for k, v in raw_row.items()}
        cleaned, _ = _parse_csv_row(normalised, i)

        # Check if email already exists in DB
        if cleaned['email'] and User.query.filter_by(email=cleaned['email']).first():
            cleaned['errors'].append('Email already exists — will be skipped')
            cleaned['duplicate'] = True
        else:
            cleaned['duplicate'] = False

        rows.append(cleaned)

    if not rows:
        flash("No data rows found in CSV.", "error")
        return redirect(url_for('admin.csv_import'))

    valid_count   = sum(1 for r in rows if not r['errors'])
    invalid_count = sum(1 for r in rows if r['errors'])

    # Store validated rows server-side in the session; don't pass them as a
    # hidden form field (which can be tampered with client-side).
    from flask import session as flask_session
    flask_session['csv_import_rows'] = [r for r in rows if not r['errors']]

    return render_template(
        "admin_csv_preview.html",
        rows=rows,
        valid_count=valid_count,
        invalid_count=invalid_count,
    )


@admin_bp.route("/clients/import/confirm", methods=["POST"])
@login_required
@admin_required
def csv_import_confirm():
    """Execute the import using the validated rows stored in the session."""
    from flask import session as flask_session

    rows = flask_session.pop('csv_import_rows', None)
    if rows is None:
        flash("Import session expired or not found. Please re-upload the CSV.", "error")
        return redirect(url_for('admin.csv_import'))

    created = 0
    skipped = 0

    for r in rows:
        # Double-check email hasn't been created since preview
        if User.query.filter_by(email=r['email']).first():
            skipped += 1
            continue
        try:
            temp_password = secrets.token_urlsafe(12)
            user = User(
                firstname=r['firstname'],
                lastname=r['lastname'],
                email=r['email'],
                role='client',
                hashed_password=generate_password_hash(temp_password),
                must_change_password=True,
                phone=r.get('phone'),
            )
            db.session.add(user)
            db.session.flush()

            client = Client(user_id=user.id)
            parts = [p for p in [r.get('address_line_1'), r.get('address_line_2'), r.get('address_line_3')] if p]
            if parts:
                client.street_address = '\n'.join(parts)
            client.postal_code        = r.get('postcode')
            client.pickup_instructions = r.get('pickup_instructions')
            has_address = bool(r.get('address_line_1'))
            db.session.add(client)
            db.session.flush()

            has_dog = r.get('has_dog') and r.get('dog_name')
            if has_dog:
                from datetime import date as date_type
                dob = date_type.fromisoformat(r['dog_dob']) if r.get('dog_dob') else None
                dog = Dog(
                    name=r['dog_name'],
                    gender=r.get('dog_gender') or 'M',
                    breed=r.get('dog_breed') or '',
                    allergies='',
                    date_of_birth=dob,
                )
                db.session.add(dog)
                db.session.flush()
                db.session.add(DogOwner(dog_id=dog.id, user_id=user.id, role='primary'))

            if has_address and has_dog:
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)

            db.session.commit()
            created += 1
        except Exception as e:
            db.session.rollback()
            logging.error(f"CSV import error for {r.get('email')}: {e}")
            skipped += 1

    if created:
        flash(f"Import complete — {created} client{'s' if created != 1 else ''} created"
              + (f", {skipped} skipped" if skipped else "") + ".", "success")
    else:
        flash("No clients were imported.", "warning")

    return redirect(url_for('admin.clients'))


@admin_bp.route("/clients/import/sample")
@login_required
@admin_required
def csv_import_sample():
    """Download a sample CSV template."""
    from flask import Response
    sample = (
        "firstname,lastname,email,phone,address_line_1,address_line_2,address_line_3,"
        "postcode,pickup_instructions,dog_name,dog_gender,dog_breed,dog_dob\n"
        "Jane,Smith,jane.smith@example.com,07700900001,12 Elm Street,Flat 2,,SE1 3QJ,"
        "\"Door code 1234, ring top bell\",Biscuit,F,Labrador,2021-03-15\n"
        "Tom,Jones,tom.jones@example.com,07700900002,,,,,,,,,\n"
    )
    return Response(
        sample,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=dogboxx_import_template.csv'}
    )


# ── Daily Messages ────────────────────────────────────────────────────────────

@admin_bp.route("/daily-messages", methods=["GET", "POST"])
@login_required
@admin_required
def daily_messages():
    """Create or update a daily message for the walker team."""
    from app.models import DailyMessage
    from datetime import date as date_type
    import bleach

    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        content = request.form.get("content", "").strip()

        if not date_str or not content:
            flash("Date and message content are required.", "danger")
            return redirect(url_for("admin.daily_messages"))

        try:
            msg_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid date format.", "danger")
            return redirect(url_for("admin.daily_messages"))

        # Sanitise HTML from Quill — allow basic formatting tags only
        allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + [
            'p', 'br', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'strong', 'em',
            'u', 's', 'blockquote', 'pre', 'code', 'a', 'span',
        ]
        allowed_attrs = {'a': ['href', 'target', 'rel'], 'span': ['class'], '*': ['class']}
        clean_content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attrs)

        msg = DailyMessage.query.filter_by(date=msg_date).first()
        now = datetime.now(timezone.utc)
        if msg:
            msg.content = clean_content
            msg.updated_at = now
        else:
            msg = DailyMessage(
                date=msg_date,
                content=clean_content,
                created_by_id=current_user.id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(msg)

        db.session.commit()
        flash(f"Message saved for {msg_date.strftime('%A, %-d %B %Y')}.", "success")
        return redirect(url_for("admin.daily_messages"))

    messages = (
        DailyMessage.query
        .order_by(DailyMessage.date.desc())
        .all()
    )
    today = datetime.now(timezone.utc).date()
    return render_template("admin_daily_messages.html", messages=messages, today=today)


@admin_bp.route("/daily-messages/<int:message_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_daily_message(message_id):
    from app.models import DailyMessage
    msg = DailyMessage.query.get_or_404(message_id)
    db.session.delete(msg)
    db.session.commit()
    flash("Message deleted.", "success")
    return redirect(url_for("admin.daily_messages"))


@admin_bp.route("/daily-messages/bulk-delete-old", methods=["POST"])
@login_required
@admin_required
def bulk_delete_old_daily_messages():
    from app.models import DailyMessage
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date()
    deleted = DailyMessage.query.filter(DailyMessage.date < cutoff).delete()
    db.session.commit()
    flash(f"Deleted {deleted} message{'s' if deleted != 1 else ''} older than 30 days.", "success")
    return redirect(url_for("admin.daily_messages"))
