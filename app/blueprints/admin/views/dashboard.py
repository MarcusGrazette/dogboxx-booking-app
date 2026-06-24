from flask import request, render_template, jsonify
from flask_login import login_required

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, Booking, Walker, WalkerSchedule, WalkerUnavailability, WalkerAdHocAvailability, ServiceType, Closure
from app import db
from app.capacity import get_max_per_walker
from sqlalchemy.orm import joinedload
from datetime import timedelta


# ─── Dashboard helpers ────────────────────────────────────────────────────────

_WALKER_COLORS = [
    '#8b5cf6',  # violet
    '#ec4899',  # pink
    '#f97316',  # orange
    '#14b8a6',  # teal
    '#3b82f6',  # blue
    '#a855f7',  # purple
    '#10b981',  # emerald
    '#f59e0b',  # amber
    '#6366f1',  # indigo
    '#84cc16',  # lime
]


def _walker_color(walker_id):
    return _WALKER_COLORS[walker_id % len(_WALKER_COLORS)]


def _walker_initials(walker):
    first = (walker.user.firstname or '')[:1].upper()
    last  = (walker.user.lastname  or '')[:1].upper()
    return (first + last) if last else first


def _slot_state(walker_id, slot, dow, schedule_map, adhoc_slots, unavail_slots):
    """Return 'available'|'unavailable'|'adhoc'|'off' for a walker+slot on a given day.

    schedule_map:  {walker_id: {dow: {slot}}}
    adhoc_slots:   set of slots this walker has ad-hoc for the date
    unavail_slots: set of slots this walker is unavailable for the date
    """
    scheduled = slot in schedule_map.get(walker_id, {}).get(dow, set())
    adhoc     = slot in adhoc_slots
    unavail   = slot in unavail_slots
    if unavail:
        return 'unavailable'
    if adhoc and not scheduled:
        return 'adhoc'
    if scheduled or adhoc:
        return 'available'
    return 'off'


def _compute_month_data(year, month, today):
    """Compute month-grid calendar data. Returns a plain (JSON-serialisable) dict."""
    from calendar import monthrange
    from datetime import date, timedelta
    from sqlalchemy import func

    _, days_in_month = monthrange(year, month)
    month_start = date(year, month, 1)
    month_end   = date(year, month, days_in_month)

    # ── Batch-load ───────────────────────────────────────────────────────────
    all_walkers = (
        Walker.query.join(User).filter(User.active == True)
        .options(joinedload(Walker.user)).all()
    )
    active_walker_ids = {w.id for w in all_walkers}

    schedules = WalkerSchedule.query.filter_by(active=True).all()
    schedule_map = {}  # {walker_id: {dow: {slot}}}
    for s in schedules:
        schedule_map.setdefault(s.walker_id, {}).setdefault(s.day_of_week, set()).add(s.slot)

    unavail_rows = WalkerUnavailability.query.filter(
        WalkerUnavailability.date >= month_start,
        WalkerUnavailability.date <= month_end,
    ).all()
    unavail_by_walker = {}  # {walker_id: {date: {slot}}}
    for u in unavail_rows:
        unavail_by_walker.setdefault(u.walker_id, {}).setdefault(u.date, set()).add(u.slot)

    adhoc_rows = WalkerAdHocAvailability.query.filter(
        WalkerAdHocAvailability.date >= month_start,
        WalkerAdHocAvailability.date <= month_end,
    ).all()
    adhoc_by_walker = {}  # {walker_id: {date: {slot}}}
    for a in adhoc_rows:
        adhoc_by_walker.setdefault(a.walker_id, {}).setdefault(a.date, set()).add(a.slot)

    closures = {
        c.date for c in Closure.query.filter(
            Closure.date >= month_start, Closure.date <= month_end,
        ).all()
    }

    raw_bookings = (
        db.session.query(
            Booking.date, Booking.slot, Booking.status,
            ServiceType.slug,
            func.count(Booking.id).label('cnt'),
        )
        .join(ServiceType)
        .filter(
            Booking.date >= month_start,
            Booking.date <= month_end,
            Booking.status.in_(Booking.ACTIVE_STATUSES),
            Booking.slot.in_(['Morning', 'Afternoon']),
        )
        .group_by(Booking.date, Booking.slot, Booking.status, ServiceType.slug)
        .all()
    )
    # {date: {slot: {status: {slug: count}}}}
    booking_map = {}
    for row in raw_bookings:
        booking_map \
            .setdefault(row.date, {}) \
            .setdefault(row.slot, {}) \
            .setdefault(row.status, {})[row.slug] = row.cnt

    walk_max = get_max_per_walker(ServiceType.WALK)

    # ── Per-day slot helper ──────────────────────────────────────────────────
    def slot_stats(d, slot_name):
        dow = d.weekday()
        n_walk = 0
        n_dropin = 0
        for w in all_walkers:
            adhoc_s   = adhoc_by_walker.get(w.id, {}).get(d, set())
            unavail_s = unavail_by_walker.get(w.id, {}).get(d, set())
            sched_s   = schedule_map.get(w.id, {}).get(dow, set())
            if (slot_name in sched_s or slot_name in adhoc_s) and slot_name not in unavail_s:
                n_walk += 1
                if w.does_drop_ins:
                    n_dropin += 1
        day_slot = booking_map.get(d, {}).get(slot_name, {})
        confirmed = day_slot.get('confirmed', {}).get(ServiceType.WALK, 0)
        pending   = day_slot.get('requested', {}).get(ServiceType.WALK, 0)
        waitlist  = day_slot.get('waitlisted', {}).get(ServiceType.WALK, 0)
        drop_ins  = sum(
            day_slot.get(st, {}).get(ServiceType.DROP_IN, 0)
            for st in ('confirmed', 'requested', 'waitlisted')
        )
        return {
            'confirmed': confirmed,
            'pending':   pending,
            'waitlist':  waitlist,
            'capacity':  n_walk * walk_max,
            'drop_ins':  drop_ins,
        }

    # ── Build unavailability spans ───────────────────────────────────────────
    unavail_dates_by_walker = {}
    for u in unavail_rows:
        unavail_dates_by_walker.setdefault(u.walker_id, set()).add(u.date)

    reason_map = {}  # {(walker_id, date): first reason found}
    for u in unavail_rows:
        key = (u.walker_id, u.date)
        if key not in reason_map:
            reason_map[key] = u.reason or 'Unavailable'

    walker_by_id = {w.id: w for w in all_walkers}
    raw_spans = []
    for wid, date_set in unavail_dates_by_walker.items():
        if wid not in walker_by_id:
            continue
        w = walker_by_id[wid]
        sorted_dates = sorted(date_set)
        span_start = span_end = sorted_dates[0]
        for d in sorted_dates[1:]:
            gap = (d - span_end).days  # 1 = consecutive, 2+ = gap days exist
            if gap == 1:
                span_end = d
            else:
                # Bridge the gap only if every intervening day is unscheduled
                # for this walker (i.e. they never work those days).  A range
                # submission like "Mon 8 – Fri 12" only creates rows for the
                # days the walker is actually scheduled, leaving gaps on the
                # days they don't normally work. We should still draw one
                # continuous bar across the whole declared period.
                gap_all_unscheduled = all(
                    not schedule_map.get(wid, {}).get(
                        (span_end + timedelta(days=offset)).weekday(), set()
                    )
                    for offset in range(1, gap)
                )
                if gap_all_unscheduled:
                    span_end = d  # extend span across unscheduled gap
                else:
                    raw_spans.append({
                        'walker_id':        wid,
                        'walker_firstname': w.user.firstname,
                        'walker_initials':  _walker_initials(w),
                        'walker_color':     _walker_color(wid),
                        'start':            span_start.isoformat(),
                        'end':              span_end.isoformat(),
                        'reason':           reason_map.get((wid, span_start), 'Unavailable'),
                    })
                    span_start = span_end = d
        raw_spans.append({
            'walker_id':        wid,
            'walker_firstname': w.user.firstname,
            'walker_initials':  _walker_initials(w),
            'walker_color':     _walker_color(wid),
            'start':            span_start.isoformat(),
            'end':              span_end.isoformat(),
            'reason':           reason_map.get((wid, span_start), 'Unavailable'),
        })

    # ── Build day cells ──────────────────────────────────────────────────────
    start_dow = month_start.weekday()
    flat_cells = [None] * (start_dow if start_dow <= 4 else 0)  # Mon–Fri align
    for i in range(days_in_month):
        d = month_start + timedelta(days=i)
        if d.weekday() >= 5:
            continue  # exclude Sat/Sun from Mon–Fri grid
        d_iso      = d.isoformat()
        is_closure = d in closures

        am = slot_stats(d, 'Morning')
        pm = slot_stats(d, 'Afternoon')
        is_closed = is_closure or (am['capacity'] == 0 and pm['capacity'] == 0)

        unavail_count = 0
        for wid in active_walker_ids:
            if d in unavail_by_walker.get(wid, {}):
                unavail_count += 1

        is_monday = (d.weekday() == 0)
        cell_spans = []
        for span in raw_spans:
            if span['start'] <= d_iso <= span['end']:
                is_start = (d_iso == span['start'])
                is_end   = (d_iso == span['end'])
                cell_spans.append({
                    **span,
                    'is_start':   is_start,
                    'is_end':     is_end,
                    'show_label': is_start or is_monday,
                })

        flat_cells.append({
            'date_str':          d_iso,
            'day_num':           d.day,
            'is_today':          d == today,
            'is_weekend':        False,
            'is_closed':         is_closed,
            'am':                am,
            'pm':                pm,
            'unavailable_count': unavail_count,
            'spans':             cell_spans,
        })

    while len(flat_cells) % 5:
        flat_cells.append(None)

    calendar_weeks = [flat_cells[i:i+5] for i in range(0, len(flat_cells), 5)]

    return {
        'year':           year,
        'month':          month,
        'month_name':     month_start.strftime('%B %Y'),
        'calendar_weeks': calendar_weeks,
    }


def _compute_day_detail(selected_date, today):
    """Compute day-detail panel data for the given date. Returns a plain dict."""
    from sqlalchemy import func

    all_walkers = (
        Walker.query.join(User).filter(User.active == True)
        .options(joinedload(Walker.user)).all()
    )

    schedules = WalkerSchedule.query.filter_by(active=True).all()
    schedule_map = {}  # {walker_id: {dow: {slot}}}
    for s in schedules:
        schedule_map.setdefault(s.walker_id, {}).setdefault(s.day_of_week, set()).add(s.slot)

    unavail_rows = WalkerUnavailability.query.filter_by(date=selected_date).all()
    unavail_by_walker = {}  # {walker_id: {slot}}
    for u in unavail_rows:
        unavail_by_walker.setdefault(u.walker_id, set()).add(u.slot)

    adhoc_rows = WalkerAdHocAvailability.query.filter_by(date=selected_date).all()
    adhoc_by_walker = {}  # {walker_id: {slot}}
    for a in adhoc_rows:
        adhoc_by_walker.setdefault(a.walker_id, set()).add(a.slot)

    raw_bookings = (
        db.session.query(
            Booking.slot, Booking.status,
            ServiceType.slug,
            func.count(Booking.id).label('cnt'),
        )
        .join(ServiceType)
        .filter(
            Booking.date == selected_date,
            Booking.status.in_(Booking.ACTIVE_STATUSES),
            Booking.slot.in_(['Morning', 'Afternoon']),
        )
        .group_by(Booking.slot, Booking.status, ServiceType.slug)
        .all()
    )
    booking_map = {}  # {slot: {status: {slug: count}}}
    for row in raw_bookings:
        booking_map.setdefault(row.slot, {}).setdefault(row.status, {})[row.slug] = row.cnt

    walker_booking_rows = (
        db.session.query(
            Booking.walker_id, Booking.slot,
            func.count(Booking.id).label('cnt'),
        )
        .join(ServiceType)
        .filter(
            Booking.date == selected_date,
            Booking.status.in_(Booking.ACTIVE_STATUSES),
            Booking.slot.in_(['Morning', 'Afternoon']),
            ServiceType.slug == ServiceType.WALK,
        )
        .group_by(Booking.walker_id, Booking.slot)
        .all()
    )
    walker_booking_map = {}  # {walker_id: {slot: count}}
    for row in walker_booking_rows:
        walker_booking_map.setdefault(row.walker_id, {})[row.slot] = row.cnt

    walk_max = get_max_per_walker(ServiceType.WALK)
    dow = selected_date.weekday()

    def slot_stats(slot_name):
        n_walk = 0
        for w in all_walkers:
            adhoc_s   = adhoc_by_walker.get(w.id, set())
            unavail_s = unavail_by_walker.get(w.id, set())
            sched_s   = schedule_map.get(w.id, {}).get(dow, set())
            if (slot_name in sched_s or slot_name in adhoc_s) and slot_name not in unavail_s:
                n_walk += 1
        slot_bk   = booking_map.get(slot_name, {})
        confirmed = slot_bk.get('confirmed', {}).get(ServiceType.WALK, 0)
        pending   = slot_bk.get('requested', {}).get(ServiceType.WALK, 0)
        waitlist  = slot_bk.get('waitlisted', {}).get(ServiceType.WALK, 0)
        drop_ins  = sum(slot_bk.get(st, {}).get(ServiceType.DROP_IN, 0)
                        for st in ('confirmed', 'requested', 'waitlisted'))
        return {
            'confirmed': confirmed,
            'pending':   pending,
            'waitlist':  waitlist,
            'capacity':  n_walk * walk_max,
            'drop_ins':  drop_ins,
        }

    am = slot_stats('Morning')
    pm = slot_stats('Afternoon')

    # Mutually-exclusive walker category, priority: unavailable > extra > scheduled > off.
    # Used both for per-row tinting and for the summary count line.
    scheduled_count   = 0
    extra_count       = 0
    unavailable_count = 0
    walker_rows = []
    for w in all_walkers:
        adhoc_s   = adhoc_by_walker.get(w.id, set())
        unavail_s = unavail_by_walker.get(w.id, set())

        am_st = _slot_state(w.id, 'Morning',   dow, schedule_map, adhoc_s, unavail_s)
        pm_st = _slot_state(w.id, 'Afternoon', dow, schedule_map, adhoc_s, unavail_s)

        if am_st == 'unavailable' or pm_st == 'unavailable':
            category = 'unavailable'
            unavailable_count += 1
        elif am_st == 'adhoc' or pm_st == 'adhoc':
            category = 'extra'
            extra_count += 1
        elif am_st == 'available' or pm_st == 'available':
            category = 'scheduled'
            scheduled_count += 1
        else:
            category = 'off'

        walker_rows.append({
            'walker_id':   w.id,
            'name':        w.user.firstname,
            'initials':    _walker_initials(w),
            'color':       _walker_color(w.id),
            'am_state':    am_st,
            'pm_state':    pm_st,
            'am_bookings': walker_booking_map.get(w.id, {}).get('Morning', 0),
            'pm_bookings': walker_booking_map.get(w.id, {}).get('Afternoon', 0),
            'category':    category,
        })

    return {
        'date_str':          selected_date.isoformat(),
        'date_display':      selected_date.strftime('%A %-d %B'),
        'is_today':          selected_date == today,
        'is_past':           selected_date < today,
        'scheduled_count':   scheduled_count,
        'extra_count':       extra_count,
        'unavailable_count': unavailable_count,
        'am':                am,
        'pm':                pm,
        'walkers':           walker_rows,
    }


@admin_bp.route("/")
@login_required
@admin_required
def index():
    """Admin dashboard — month-grid calendar with day-detail side panel."""
    from datetime import date

    today = date.today()

    sel_str = request.args.get('date')
    try:
        selected_date = date.fromisoformat(sel_str)
    except (TypeError, ValueError):
        selected_date = today

    mon_str = request.args.get('month', '')
    try:
        vy, vm = mon_str.split('-')
        view_year, view_month = int(vy), int(vm)
        if not (1 <= view_month <= 12):
            raise ValueError
    except (ValueError, AttributeError):
        view_year, view_month = selected_date.year, selected_date.month

    month_data = _compute_month_data(view_year, view_month, today)
    day_detail = _compute_day_detail(selected_date, today)

    return render_template(
        "admin.html",
        today_iso=today.isoformat(),
        selected_date_iso=selected_date.isoformat(),
        month_data=month_data,
        day_detail=day_detail,
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
            Booking.status.in_(Booking.ACTIVE_STATUSES),
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


@admin_bp.route("/api/month-data")
@login_required
@admin_required
def api_month_data():
    """Return month-grid calendar data as JSON for client-side navigation."""
    from datetime import date
    try:
        year  = int(request.args.get('year',  date.today().year))
        month = int(request.args.get('month', date.today().month))
        if not (1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(error='Invalid year/month'), 400
    today = date.today()
    return jsonify(_compute_month_data(year, month, today))


@admin_bp.route("/api/day-detail")
@login_required
@admin_required
def api_day_detail():
    """Return day-detail panel data as JSON."""
    from datetime import date
    try:
        selected_date = date.fromisoformat(request.args.get('date', ''))
    except ValueError:
        selected_date = date.today()
    today = date.today()
    return jsonify(_compute_day_detail(selected_date, today))


@admin_bp.route("/api/board-chart-data")
@login_required
@admin_required
def board_chart_data():
    """Return 7-day booking chart data for the week (Mon–Sun) containing ?date=YYYY-MM-DD.

    Optional ?service=group-walk|drop-in filters by service type.
    """
    from datetime import date, timedelta
    from sqlalchemy import func

    date_str    = request.args.get('date')
    service_slug = request.args.get('service')
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

    q = Booking.query.filter(
        Booking.date >= week_start,
        Booking.date <= chart_end,
        Booking.status.in_(Booking.ACTIVE_STATUSES),
        Booking.slot.in_(['Morning', 'Afternoon']),
    )
    if service_slug:
        svc = ServiceType.query.filter_by(slug=service_slug, active=True).first()
        if svc:
            q = q.filter(Booking.service_type_id == svc.id)

    chart_bookings = (
        q
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
