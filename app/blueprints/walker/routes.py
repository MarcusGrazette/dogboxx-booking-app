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


def _build_daily_overview(selected_date):
    """Return the day's bookings grouped by slot → walker for the overview view.

    Shape:
        {
          'Morning':   {'walker_groups': [...], 'dog_count': N},
          'Afternoon': {'walker_groups': [...], 'dog_count': N},
        }

    Each walker group is {'walker': Walker, 'is_drop_in': bool, 'bookings': [Booking, ...]}.
    Drop-in and group-walk assignments for the same walker appear as separate
    groups so the DROP-IN badge is unambiguous. Walkers with no bookings in a
    slot are omitted — only those actually working show up.
    """
    bookings = (
        Booking.query
        .options(
            joinedload(Booking.dog),
            joinedload(Booking.walker).joinedload(Walker.user),
            joinedload(Booking.service_type),
        )
        .filter(
            Booking.date == selected_date,
            Booking.status.in_(Booking.WALKER_STATUSES),
            Booking.walker_id.isnot(None),
        )
        .order_by(Booking.slot, Booking.pickup_order)
        .all()
    )

    def _is_drop_in(b):
        return b.service_type and b.service_type.slug == ServiceType.DROP_IN

    overview = {}
    for slot in ('Morning', 'Afternoon'):
        slot_bookings = [b for b in bookings if b.slot == slot]

        # Group by (walker_id, is_drop_in) so a walker doing both shows two cards
        groups = {}
        for b in slot_bookings:
            key = (b.walker_id, _is_drop_in(b))
            groups.setdefault(key, {
                'walker': b.walker,
                'is_drop_in': _is_drop_in(b),
                'bookings': [],
            })['bookings'].append(b)

        # Sort: group walks first, drop-ins after; within each, walker first name
        walker_groups = sorted(
            groups.values(),
            key=lambda g: (g['is_drop_in'], (g['walker'].user.firstname or '').lower()),
        )

        overview[slot] = {
            'walker_groups': walker_groups,
            'dog_count': sum(len(g['bookings']) for g in walker_groups),
        }
    return overview


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

    schedule_groups = _build_schedule_change_groups(
        adhoc_availabilities, unavailabilities
    )

    return render_template("walker_profile.html",
                           walker=walker,
                           schedule_grid=schedule_grid,
                           schedule_groups=schedule_groups,
                           today=today)


@walker_bp.route("/api/schedule-changes")
@login_required
@walker_required
def api_schedule_changes():
    """Return the merged + grouped schedule-changes list HTML partial.

    Called after every add or delete via the unified form so the JS doesn't
    have to mirror the server-side grouping logic when re-rendering.
    """
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return "Walker not found", 404

    today = datetime.now(timezone.utc).date()
    unavailabilities = WalkerUnavailability.query.filter(
        WalkerUnavailability.walker_id == walker.id,
        WalkerUnavailability.date >= today,
    ).order_by(WalkerUnavailability.date, WalkerUnavailability.slot).all()

    adhoc_availabilities = WalkerAdHocAvailability.query.filter(
        WalkerAdHocAvailability.walker_id == walker.id,
        WalkerAdHocAvailability.date >= today,
    ).order_by(WalkerAdHocAvailability.date, WalkerAdHocAvailability.slot).all()

    schedule_groups = _build_schedule_change_groups(
        adhoc_availabilities, unavailabilities
    )
    return render_template(
        "partials/walker_schedule_changes_list.html",
        schedule_groups=schedule_groups,
    )


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

    # Any confirmed bookings this walker held for this date/slot are no longer
    # guaranteed — reset them to requested so they surface as pending on the board.
    affected = Booking.query.filter_by(
        walker_id=walker.id, date=unavail_date, slot=slot, status='confirmed',
    ).all()
    for b in affected:
        b.walker_id = None
        b.status = 'requested'

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
    reason = (data.get('reason') or '').strip() or None

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
        reason=reason,
    )
    db.session.add(adhoc)
    db.session.commit()

    return jsonify(success=True, message="Ad hoc availability added", adhoc=adhoc.to_dict()), 201


# ── Batch range endpoints (powers the unified Schedule changes form) ─────────

MAX_RANGE_DAYS = 90


def _build_schedule_change_groups(adhoc_rows, unavail_rows):
    """Merge adhoc + unavailability rows and collapse contiguous (type, reason,
    slot-set) runs into a single display row.

    Two passes:
      1) Per-day key (date, type, reason) collects the slot set + the source
         row IDs. Morning+Afternoon on the same day become slot_label='Both'.
      2) Walk the per-day rows in date order and join calendar-adjacent days
         that share (type, reason, slot_set) into one display range.

    Calendar-adjacent (date+1) is intentionally strict — a gap of even one day
    breaks the range. Saturday/Sunday don't appear (the batch endpoint refuses
    to create them) so a Mon–Fri run can extend into the next week only via a
    same-day entry on Monday, which would be calendar-non-contiguous and so
    correctly remains a separate group.
    """
    from collections import defaultdict

    per_day = defaultdict(
        lambda: {'slots': set(), 'adhoc_ids': [], 'unavail_ids': []}
    )
    for r in adhoc_rows:
        key = (r.date, 'available', r.reason)
        per_day[key]['slots'].add(r.slot)
        per_day[key]['adhoc_ids'].append(r.id)
    for r in unavail_rows:
        key = (r.date, 'unavailable', r.reason)
        per_day[key]['slots'].add(r.slot)
        per_day[key]['unavail_ids'].append(r.id)

    # Per-day rows sorted by date (then type for determinism on same-date ties)
    day_rows = []
    for (d, t, reason), payload in sorted(per_day.items(),
                                          key=lambda x: (x[0][0], x[0][1])):
        slot_set = frozenset(payload['slots'])
        if slot_set == {'Morning', 'Afternoon'}:
            slot_label = 'Both'
        elif 'Morning' in slot_set:
            slot_label = 'Morning'
        else:
            slot_label = 'Afternoon'
        day_rows.append({
            'type': t,
            'date': d,
            'slot_set': slot_set,
            'slot_label': slot_label,
            'reason': reason,
            'adhoc_ids': list(payload['adhoc_ids']),
            'unavail_ids': list(payload['unavail_ids']),
        })

    groups = []
    for row in day_rows:
        last = groups[-1] if groups else None
        if (last
                and last['type'] == row['type']
                and last['reason'] == row['reason']
                and last['_slot_set'] == row['slot_set']
                and (row['date'] - last['end_date']).days == 1):
            last['end_date'] = row['date']
            last['adhoc_ids'].extend(row['adhoc_ids'])
            last['unavail_ids'].extend(row['unavail_ids'])
        else:
            groups.append({
                'type': row['type'],
                'start_date': row['date'],
                'end_date': row['date'],
                'slot_label': row['slot_label'],
                'reason': row['reason'],
                '_slot_set': row['slot_set'],
                'adhoc_ids': list(row['adhoc_ids']),
                'unavail_ids': list(row['unavail_ids']),
            })

    for g in groups:
        g['is_range'] = g['start_date'] != g['end_date']
        del g['_slot_set']
    return groups


def _slots_walker_works(walker_id, day_of_week):
    """Return set of slots the walker has in their default weekly schedule."""
    rows = WalkerSchedule.query.filter_by(
        walker_id=walker_id, day_of_week=day_of_week, active=True
    ).all()
    return {r.slot for r in rows}


@walker_bp.route("/schedule-changes/batch", methods=["POST"])
@login_required
@walker_required
def schedule_changes_batch():
    """Create schedule-change entries across a date range and slot list.

    JSON body:
        {
          "start_date": "YYYY-MM-DD",
          "end_date":   "YYYY-MM-DD",   # optional, defaults to start_date
          "slots":      ["Morning"] | ["Afternoon"] | ["Morning","Afternoon"],
          "type":       "available" | "unavailable",
          "reason":     "Annual leave" # optional
        }

    Iterates (date in range, slot in slots) and either:
      • type=available    → creates WalkerAdHocAvailability if the walker is
        NOT in the default schedule for that day/slot (and not already adhoc)
      • type=unavailable  → creates WalkerUnavailability if the walker IS
        scheduled for that day/slot (and not already marked)

    Weekends (Saturday + Sunday) are always skipped — DogBoxx is Mon–Fri.
    All slots either-applied or all-skipped are reported in the response so
    the walker sees what actually happened.
    """
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return jsonify(success=False, message="Walker profile not found"), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    start_str = data.get('start_date') or data.get('date')
    end_str = data.get('end_date') or start_str
    slots = data.get('slots') or []
    change_type = data.get('type')
    reason = (data.get('reason') or '').strip() or None

    # ── Validation ────────────────────────────────────────────────────────
    if not start_str:
        return jsonify(success=False, message="Start date is required."), 400
    if change_type not in ('available', 'unavailable'):
        return jsonify(success=False, message="Type must be 'available' or 'unavailable'."), 400
    if not slots or any(s not in ('Morning', 'Afternoon') for s in slots):
        return jsonify(success=False, message="Pick at least one valid slot."), 400

    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date format (use YYYY-MM-DD)."), 400

    today = datetime.now(timezone.utc).date()
    if start_date < today:
        return jsonify(success=False, message="Start date cannot be in the past."), 400
    if end_date < start_date:
        return jsonify(success=False, message="End date must be on or after the start date."), 400
    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        return jsonify(success=False,
                       message=f"Range too long — please pick a span of {MAX_RANGE_DAYS} days or fewer."), 400

    # ── Iterate and apply ─────────────────────────────────────────────────
    created_adhoc_ids = []
    created_unavail_ids = []
    skipped_reasons = []
    skipped = 0

    current = start_date
    while current <= end_date:
        # Skip weekends — DogBoxx is a Mon–Fri business
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        scheduled_slots = _slots_walker_works(walker.id, current.weekday())
        date_label = current.strftime('%a %-d %b')

        for slot in slots:
            if change_type == 'unavailable':
                if slot not in scheduled_slots:
                    skipped += 1
                    skipped_reasons.append(
                        f"{date_label} {slot} — you're not normally scheduled."
                    )
                    continue
                exists = WalkerUnavailability.query.filter_by(
                    walker_id=walker.id, date=current, slot=slot
                ).first()
                if exists:
                    skipped += 1
                    continue
                row = WalkerUnavailability(
                    walker_id=walker.id, date=current, slot=slot, reason=reason
                )
                db.session.add(row)
                db.session.flush()
                created_unavail_ids.append(row.id)

                # Reset any confirmed bookings for this slot back to requested.
                for b in Booking.query.filter_by(
                    walker_id=walker.id, date=current, slot=slot, status='confirmed',
                ).all():
                    b.walker_id = None
                    b.status = 'requested'
            else:  # available
                if slot in scheduled_slots:
                    skipped += 1
                    skipped_reasons.append(
                        f"{date_label} {slot} — you're already scheduled."
                    )
                    continue
                exists = WalkerAdHocAvailability.query.filter_by(
                    walker_id=walker.id, date=current, slot=slot
                ).first()
                if exists:
                    skipped += 1
                    continue
                row = WalkerAdHocAvailability(
                    walker_id=walker.id, date=current, slot=slot, reason=reason
                )
                db.session.add(row)
                db.session.flush()
                created_adhoc_ids.append(row.id)

        current += timedelta(days=1)

    db.session.commit()

    created = len(created_adhoc_ids) + len(created_unavail_ids)
    return jsonify(
        success=True,
        created=created,
        skipped=skipped,
        skipped_reasons=skipped_reasons[:10],  # cap to keep response small
        message=(
            f"{created} slot{'s' if created != 1 else ''} added."
            + (f" {skipped} skipped." if skipped else "")
        ),
        adhoc_ids=created_adhoc_ids,
        unavail_ids=created_unavail_ids,
    )


@walker_bp.route("/schedule-changes/batch-delete", methods=["POST"])
@login_required
@walker_required
def schedule_changes_batch_delete():
    """Delete multiple schedule-change entries in one request.

    JSON body:
        {
          "adhoc_ids":   [1, 2, 3],   # optional
          "unavail_ids": [4, 5]        # optional
        }
    Only rows owned by the current walker are deleted; others are silently
    ignored. Returns the count actually deleted.
    """
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return jsonify(success=False, message="Walker profile not found"), 404

    data = request.get_json(silent=True) or {}
    adhoc_ids = data.get('adhoc_ids') or []
    unavail_ids = data.get('unavail_ids') or []

    if not isinstance(adhoc_ids, list) or not isinstance(unavail_ids, list):
        return jsonify(success=False, message="Invalid IDs."), 400

    deleted = 0
    if adhoc_ids:
        deleted += WalkerAdHocAvailability.query.filter(
            WalkerAdHocAvailability.id.in_(adhoc_ids),
            WalkerAdHocAvailability.walker_id == walker.id,
        ).delete(synchronize_session=False)
    if unavail_ids:
        deleted += WalkerUnavailability.query.filter(
            WalkerUnavailability.id.in_(unavail_ids),
            WalkerUnavailability.walker_id == walker.id,
        ).delete(synchronize_session=False)

    db.session.commit()
    return jsonify(success=True, deleted=deleted)


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
    """Show pickup list (default) or daily overview for a given date.

    Query param view=overview switches to the team-wide overview; default
    is the walker's own pickup list. Date can be passed as a path segment
    /pickups/<date_str> or via ?date=YYYY-MM-DD.
    """
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        flash("Walker profile not found. Please contact support.", "danger")
        return redirect(url_for('client.index'))

    today = datetime.now(timezone.utc).date()

    # Date resolution: path param wins, then ?date=, then today
    raw_date = date_str or request.args.get('date')
    if raw_date:
        try:
            selected_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            flash("Invalid date format.", "danger")
            return redirect(url_for('walker.pickups'))
    else:
        selected_date = today

    view = request.args.get('view', 'pickups')
    if view not in ('pickups', 'overview'):
        view = 'pickups'

    daily_message = DailyMessage.query.filter_by(date=selected_date).first()

    # Shared template context — view-specific data added below
    ctx = dict(
        walker=walker,
        selected_date=selected_date,
        today=today,
        view=view,
        daily_message=daily_message,
    )

    if view == 'overview':
        ctx['overview'] = _build_daily_overview(selected_date)
        ctx['has_pickups'] = any(s['dog_count'] for s in ctx['overview'].values())
        return render_template("walker_pickups.html", **ctx)

    # Default: walker's own pickup list
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
    ctx['morning_drop_ins']   = [b for b in bookings if b.slot == 'Morning'   and     _is_drop_in(b)]
    ctx['morning_pickups']    = [b for b in bookings if b.slot == 'Morning'   and not _is_drop_in(b)]
    ctx['afternoon_pickups']  = [b for b in bookings if b.slot == 'Afternoon' and not _is_drop_in(b)]
    ctx['afternoon_drop_ins'] = [b for b in bookings if b.slot == 'Afternoon' and     _is_drop_in(b)]
    ctx['has_pickups'] = len(bookings) > 0
    ctx['double_booked_dog_ids'] = _double_booked_dog_ids(selected_date)

    return render_template("walker_pickups.html", **ctx)


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


@walker_bp.route("/api/daily-overview/<date_str>")
@login_required
@walker_required
def api_daily_overview(date_str):
    """Return the daily-overview HTML partial for a given date."""
    walker = Walker.query.filter_by(user_id=current_user.id).first()
    if not walker:
        return "Walker not found", 404

    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return "Invalid date", 400

    overview = _build_daily_overview(selected_date)
    has_pickups = any(s['dog_count'] for s in overview.values())
    daily_message = DailyMessage.query.filter_by(date=selected_date).first()

    return render_template("partials/daily_overview.html",
                           walker=walker,
                           selected_date=selected_date,
                           overview=overview,
                           has_pickups=has_pickups,
                           daily_message=daily_message)



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
