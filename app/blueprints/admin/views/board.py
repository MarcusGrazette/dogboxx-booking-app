from flask import request, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError
from datetime import datetime, timezone, timedelta
import logging
import traceback

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.utils.db_error_handler import handle_db_errors
from app.models import User, Booking, Walker, WalkerSchedule, WalkerUnavailability, WalkerAdHocAvailability, ServiceType
from app import db
from app.capacity import get_max_per_walker, get_walker_slot_count, get_drop_in_capacity, auto_assign_walker, get_available_walkers, check_availability, acquire_booking_lock
from app.utils.notifications import create_notification
from app.utils.booking_status import transition_booking


def _booking_dict(b, both_slots_dog_ids=None):
    d = {
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
    if both_slots_dog_ids is not None:
        d['has_both_slots'] = b.dog_id in both_slots_dog_ids
    return d


@admin_bp.route("/board")
@login_required
@admin_required
def board():
    """Group walk assignment board — click-to-assign + drag-to-reorder."""
    return render_template("admin_board.html")


@admin_bp.route("/board-fragment")
@login_required
@admin_required
def board_fragment():
    """Row 2 of the assignment board, served as a bare HTML fragment.
    Used by the dashboard Assign modal — caller wires up createBoard() with
    the date it wants. No admin layout, no calendar widget, no chart."""
    return render_template("partials/admin_board_row2.html")


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
    from sqlalchemy import func
    rows = (
        db.session.query(ServiceType.slug, func.count(Booking.id))
        .join(Booking, Booking.service_type_id == ServiceType.id)
        .filter(
            ServiceType.slug.in_([ServiceType.WALK, ServiceType.DROP_IN]),
            Booking.status.in_(Booking.PENDING_STATUSES),
        )
        .group_by(ServiceType.slug)
        .all()
    )
    counts = {slug: cnt for slug, cnt in rows}
    return jsonify(
        group_walks=counts.get(ServiceType.WALK, 0),
        drop_ins=counts.get(ServiceType.DROP_IN, 0),
    )


@admin_bp.route("/api/drop-in-board-data/<date_str>")
@login_required
@admin_required
def drop_in_board_data(date_str):
    """JSON board data for drop-in bookings on a given date."""
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    drop_in_service = ServiceType.query.filter_by(slug=ServiceType.DROP_IN).first()
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
        .join(User, Walker.user_id == User.id)
        .filter(Walker.id.in_(all_board_walker_ids), User.active == True)
        .all()
    ) if all_board_walker_ids else []

    pending  = [_booking_dict(b) for b in all_bookings if b.status in ('requested', 'waitlisted')]
    assigned = [_booking_dict(b) for b in all_bookings if b.walker_id and b.status == 'confirmed']

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

    max_capacity = get_max_per_walker(ServiceType.DROP_IN)

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

    try:
        group_walk_service = ServiceType.query.filter_by(slug=ServiceType.WALK).first()
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
            .join(User, Walker.user_id == User.id)
            .filter(Walker.id.in_(all_board_walker_ids), User.active == True)
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

        pending   = [_booking_dict(b, both_slots_dog_ids) for b in all_bookings if b.status in ('requested', 'waitlisted')]
        assigned  = [_booking_dict(b, both_slots_dog_ids) for b in all_bookings if b.walker_id and b.status == 'confirmed']

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

        max_capacity = get_max_per_walker(ServiceType.WALK)

        return jsonify(
            success=True,
            date=date_str,
            pending=pending,
            assigned=assigned,
            walkers=walkers_data,
            max_capacity=max_capacity,
        )
    except Exception as e:
        logging.error('Error loading board data for %s: %s', date_str, e)
        return jsonify(success=False, message="Could not load board data"), 500


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
    slot = data.get("slot")
    slot_override = bool(data.get("slot_override"))

    try:
        if not booking_id:
            return jsonify(success=False, message="No booking ID provided"), 400

        booking = db.session.get(Booking, booking_id)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404

        # If walker_id is None, this is an unassignment operation
        if walker_id is None:
            transition_booking(booking, 'requested', actor_id=current_user.id,
                               walker_id=None)
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

        # Check walker is available for this date+slot (skip if admin explicitly overriding slot).
        # Delegating to get_available_walkers() keeps this in lock-step with the rest of the
        # app — it accounts for default schedule + ad-hoc availability minus unavailability.
        # The previous inline check only consulted WalkerSchedule, so an admin who'd added an
        # ad-hoc override for the walker would still be blocked, and a walker marked
        # unavailable for a slot could still be assigned to it without a clear warning.
        assign_slot = slot or booking.slot
        if not slot_override:
            service_slug = booking.service_type.slug if booking.service_type else ServiceType.WALK
            is_drop_in = (service_slug == ServiceType.DROP_IN)
            available_walkers = get_available_walkers(
                booking.date, assign_slot, drop_in=is_drop_in
            )
            available_ids = {w.id for w in available_walkers}
            if walker.id not in available_ids:
                # Distinguish "not scheduled" from "scheduled but marked unavailable"
                # so the admin sees a useful message.
                marked_off = WalkerUnavailability.query.filter_by(
                    walker_id=walker.id, date=booking.date, slot=assign_slot,
                ).first()
                if marked_off:
                    return jsonify(success=False, message=f"{walker.user.firstname} is marked unavailable for {assign_slot} on this day"), 400
                return jsonify(success=False, message=f"{walker.user.firstname} is not scheduled for {assign_slot} on this day"), 400

        # Check walker capacity for the given slot and date (scoped to same service type).
        # Booking.id != booking.id is correct in all reassignment cases:
        #   A→B (different walkers): booking is on A, not B, so it wouldn't be counted
        #     in B's query anyway — the exclusion is redundant but harmless.
        #   A→A (same walker, slot_override): booking IS on A for the old slot, so
        #     excluding it correctly avoids counting it when checking A's new-slot capacity.
        if slot:
            service_slug = booking.service_type.slug if booking.service_type else ServiceType.WALK
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

        # If slot is being overridden, check the dog doesn't already have an active booking for the target slot
        old_slot = booking.slot
        if slot_override and slot and old_slot != slot:
            conflict = Booking.query.filter(
                Booking.dog_id == booking.dog_id,
                Booking.date == booking.date,
                Booking.slot == slot,
                Booking.status.notin_(('cancelled', 'rejected', 'completed')),
                Booking.id != booking.id,
            ).first()
            if conflict:
                return jsonify(
                    success=False,
                    message=f"{booking.dog.name} already has an active {slot.lower()} booking on this date"
                ), 409

        # Update walker assignment and slot. transition_booking sets status,
        # confirmed_at, walker_id and logs the BSC row (actor = the admin).
        # On a slot override, record the move in the BSC notes (F6) so the
        # activity feed can tell a slot change apart from a plain re-confirm
        # (otherwise both look like a confirmed→confirmed row with no detail).
        slot_was_changed = bool(slot_override and slot and old_slot and old_slot != slot)
        bsc_notes = f"slot {old_slot} → {slot}" if slot_was_changed else None
        transition_booking(booking, 'confirmed', actor_id=current_user.id,
                           walker_id=walker.id, notes=bsc_notes,
                           old_slot=old_slot if slot_was_changed else None,
                           new_slot=slot if slot_was_changed else None)
        if slot:
            booking.slot = slot

        # Notify client + walker — label differs by service type
        date_str_fmt = booking.date.strftime('%a %-d %b')
        dog_name = booking.dog.name if booking.dog else 'your dog'
        service_label = 'drop-in' if (booking.service_type and booking.service_type.slug == ServiceType.DROP_IN) else 'walk'
        walker_first = walker.user.firstname if walker.user else None

        # Send slot-change notification if the slot was overridden
        if slot_was_changed:
            create_notification(
                recipient_id=booking.user_id,
                notification_type='system',
                title=f"{dog_name}'s {service_label} on {date_str_fmt} has been moved to {slot.lower()}",
                body=f'Originally booked for {old_slot.lower()}.',
                link='/',
                sender_id=current_user.id,
            )

        if not slot_was_changed:
            create_notification(
                recipient_id=booking.user_id,
                notification_type='booking_confirmed',
                title=f"{dog_name}'s {booking.slot.lower()} {service_label} on {date_str_fmt} has been confirmed",
                body=f'Booked with {walker_first}.' if walker_first else 'Walker assigned.',
                link='/',
                sender_id=current_user.id,
            )

        if walker.user_id != current_user.id:
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


@admin_bp.route("/booking/<int:booking_id>/decline", methods=["POST"])
@login_required
@admin_required
def decline_booking(booking_id):
    """Decline a pending or waitlisted booking. Sets status to 'rejected' and notifies the client."""
    try:
        booking = db.session.get(Booking, booking_id)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404
        if booking.status not in Booking.PENDING_STATUSES:
            return jsonify(success=False, message="Only pending or waitlisted bookings can be declined"), 400

        # transition_booking sets status, cancelled_at and logs the BSC row.
        transition_booking(booking, 'rejected', actor_id=current_user.id,
                           cancelled_by='admin')

        service_label = 'drop-in' if (booking.service_type and booking.service_type.slug == ServiceType.DROP_IN) else 'walk'
        dog_name = booking.dog.name if booking.dog else 'your dog'
        date_str = booking.date.strftime('%a %-d %b')

        create_notification(
            recipient_id=booking.user_id,
            notification_type='booking_cancelled',
            title=f"{dog_name}'s {booking.slot.lower()} {service_label} on {date_str} has been declined",
            body="Please get in touch if you'd like to discuss.",
            link='/',
            sender_id=current_user.id,
        )

        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        logging.error('Error declining booking %s: %s', booking_id, e)
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

    service_slug = request.args.get('service')

    # Get bookings for the month
    q = Booking.query.filter(
        Booking.date >= start_date,
        Booking.date < end_date,
        Booking.status != 'cancelled'
    )
    if service_slug:
        q = q.join(ServiceType).filter(ServiceType.slug == service_slug)
    bookings = q.all()

    # Group by date
    booking_counts = {}
    pending_dates = set()

    for booking in bookings:
        date_str = booking.date.strftime('%Y-%m-%d')
        date_day = booking.date.day

        if date_str not in booking_counts:
            booking_counts[date_str] = {'total': 0, 'assigned': 0}
        booking_counts[date_str]['total'] += 1

        if booking.walker_id:
            booking_counts[date_str]['assigned'] += 1
        elif booking.status in Booking.PENDING_STATUSES:
            pending_dates.add(date_day)

    return jsonify(success=True, data=booking_counts, pending_dates=list(pending_dates))
