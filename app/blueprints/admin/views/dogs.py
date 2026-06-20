from flask import request, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone
import logging
import uuid

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, Booking, Dog, DogOwner, ServiceType
from app import db
from app.capacity import get_walker_slot_count, auto_assign_walker, check_availability, acquire_booking_lock
from app.utils.notifications import NotificationBatch
from app.utils.booking_status import transition_booking, record_booking_created, bulk_transition
from app.services.booking_service import create_booking, CapacityError
from app.utils.invoicing import is_late_cancellation


def _parse_day_filter(raw_values):
    """Parse repeated 'days' params into a set of weekday ints (0=Mon..4=Fri).
    Bogus values are silently dropped, matching the slot-filter approach."""
    out = set()
    for v in raw_values:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 4:
            out.add(n)
    return out


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
                     User.email.label('owner_email'),
                     User.profile_pic.label('owner_pic'))
        .order_by(Dog.name)
        .all()
    )
    dogs_data = [
        {
            'dog': row[0],
            'owner_user_id': row[1],
            'owner_name': f"{row[2]} {row[3]}",
            'owner_email': row[4],
            'owner_pic': row[5],
        }
        for row in rows
    ]
    from datetime import date as date_type
    service_types = (
        ServiceType.query
        .filter_by(active=True, slot_type='morning_afternoon')
        .order_by(ServiceType.name)
        .all()
    )
    return render_template(
        "admin_dogs.html",
        dogs_data=dogs_data,
        today=date_type.today(),
        service_types=service_types,
    )


@admin_bp.route("/dogs/<int:dog_id>/update", methods=["POST"])
@login_required
@admin_required
def update_dog(dog_id):
    """AJAX: update a dog's details from the admin dogs table."""
    from datetime import date as date_type
    dog = db.session.get(Dog, dog_id)
    if not dog:
        return jsonify(success=False, message="Dog not found"), 404

    data = request.get_json()
    if not data:
        return jsonify(success=False, message="No data received"), 400

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(success=False, message="Name is required"), 400

    gender = (data.get('gender') or '').strip()
    if gender not in ('male', 'female', ''):
        return jsonify(success=False, message="Invalid gender"), 400

    dob_str = (data.get('date_of_birth') or '').strip()
    dob = None
    if dob_str:
        try:
            dob = datetime.strptime(dob_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date of birth"), 400

    dog.name = name
    dog.gender = gender or dog.gender
    dog.breed = (data.get('breed') or '').strip()
    dog.date_of_birth = dob
    dog.allergies = (data.get('allergies') or '').strip()
    dog.pickup_instructions = (data.get('pickup_instructions') or '').strip() or None
    dog.whatsapp_group_url = (data.get('whatsapp_group_url') or '').strip() or None
    dog.hold_key = bool(data.get('hold_key'))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating dog {dog_id}: {e}")
        return jsonify(success=False, message="Failed to save changes"), 500

    return jsonify(success=True, name=dog.name, breed=dog.breed or '—')


@admin_bp.route("/book_for_dog", methods=["POST"])
@login_required
@admin_required
def book_for_dog():
    """Admin: create a single booking on behalf of a dog's owner."""
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        dog_id          = data.get('dog_id')
        user_id         = data.get('user_id')
        date_str        = data.get('date', '')
        slot            = data.get('slot', '')
        service_type_id = data.get('service_type_id')

        if not all([dog_id, user_id, date_str, slot]):
            return jsonify(success=False, message="Missing required fields"), 400

        # Resolve service — default to Walk for back-compat if not supplied.
        if service_type_id:
            service = db.session.get(ServiceType, service_type_id)
            if not service or not service.active or service.slot_type != 'morning_afternoon':
                return jsonify(success=False, message="Invalid service type"), 400
        else:
            service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
            if not service:
                return jsonify(success=False, message="No service type available"), 400

        is_drop_in = service.slug == ServiceType.DROP_IN

        from datetime import date as date_type
        try:
            booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date format"), 400

        # Admin bookings allow any date — past, today, or future. Used for
        # back-filling missed bookings (so they hit invoicing) or recording a
        # same-day walk that wasn't booked in advance. Closure / capacity /
        # duplicate-active-booking checks downstream still apply.

        valid_slots = ('Morning', 'Afternoon') if is_drop_in else ('Morning', 'Afternoon', 'Both')
        if slot not in valid_slots:
            return jsonify(success=False, message="Invalid slot"), 400

        dog = db.session.get(Dog, dog_id)
        if not dog:
            return jsonify(success=False, message="Dog not found"), 404

        slots_to_book = ['Morning', 'Afternoon'] if slot == 'Both' else [slot]

        # Duplicate check for all slots before creating any. The active-booking
        # uniqueness index treats any service type the same, so a drop-in
        # collides with an existing walk in the same slot (and vice versa).
        active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
        for s in slots_to_book:
            existing = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == booking_date,
                Booking.slot == s,
                Booking.status.in_(active_statuses)
            ).first()
            if existing:
                label = f"the {s.lower()} slot" if slot == 'Both' else "that slot"
                return jsonify(success=False, message=f"This dog already has a booking for {label} on that date"), 400

        service_label = service.name.lower()

        bookings_created = []
        # Shared batch_id so the feed can cluster a single admin booking action
        # (e.g. 'Both' slots) — NOTIFICATIONS.md §9.2, D4.
        batch_id = uuid.uuid4().hex
        for s in slots_to_book:
            try:
                booking, _ = create_booking(
                    dog=dog, user_id=user_id, date=booking_date, slot=s,
                    service=service, actor_id=current_user.id, batch_id=batch_id,
                    admin_override=True, created_by_id=current_user.id,
                    # Drop-ins are never auto-assigned — land as requested/waitlisted
                    # for manual admin confirmation, matching client/book_drop_in.
                    auto_confirm=not is_drop_in,
                )
            except CapacityError as e:
                return jsonify(success=False, message=str(e)), 400

            bookings_created.append(booking)

        db.session.flush()  # populate booking.ids before notifications

        date_str_fmt     = booking_date.strftime('%a %-d %b')
        admin_first      = current_user.firstname or 'Admin'
        admin_books_self = (current_user.id == int(user_id))
        is_past          = booking_date < date_type.today()
        # 'walk' / 'drop-in' is the canonical client- AND walker-facing label
        # (§7.7). service.name ("Group Walk" / "Drop In") stays admin-facing
        # (the JSON response message below).
        svc_label    = 'drop-in' if is_drop_in else 'walk'
        client_actor = None if admin_books_self else admin_first
        # Grouped notifications (§9.3/§9.4): AM+PM of one booking action collapse
        # into a single bell entry per recipient + status.
        batch = NotificationBatch(actor_id=current_user.id)
        for b in bookings_created:
            walker_first = b.walker.user.firstname if b.walker and b.walker.user else None
            if b.status == 'confirmed':
                batch.add(user_id, 'booking_confirmed', actor_first=client_actor,
                          dog_name=dog.name, slot=b.slot, date=booking_date,
                          svc_label=svc_label, walker_name=walker_first)
                # Skip the walker ping for past dates — the walk already happened
                # (or didn't); pinging the walker about it is noise.
                if b.walker and b.walker.user_id != current_user.id and not is_past:
                    batch.add(b.walker.user_id, 'walker_assigned',
                              dog_name=dog.name, slot=b.slot, date=booking_date,
                              svc_label=svc_label)
            elif b.status == 'waitlisted':
                # §7.3: admin-made pending bookings now reach the client.
                batch.add(user_id, 'booking_waitlisted', actor_first=client_actor,
                          dog_name=dog.name, slot=b.slot, date=booking_date,
                          svc_label=svc_label)
            else:
                batch.add(user_id, 'booking_requested', actor_first=client_actor,
                          dog_name=dog.name, slot=b.slot, date=booking_date,
                          svc_label=svc_label)

        batch.flush()
        db.session.commit()

        if len(bookings_created) == 1:
            b = bookings_created[0]
            return jsonify(success=True, status=b.status,
                           message=f"{service.name} {b.status} for {dog.name} on {date_str_fmt}")
        else:
            statuses = [b.status for b in bookings_created]
            if len(set(statuses)) == 1:
                msg = f"Both {service_label}s {statuses[0]} for {dog.name} on {date_str_fmt}"
            else:
                parts = [f"{b.slot}: {b.status}" for b in bookings_created]
                msg = f"{service.name} bookings for {dog.name} on {date_str_fmt} — {', '.join(parts)}"
            return jsonify(success=True, status=statuses[0], message=msg)

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in admin book_for_dog: {e}")
        return jsonify(success=False, message="Server error"), 500


@admin_bp.route("/recurring_for_dog", methods=["POST"])
@login_required
@admin_required
def recurring_for_dog():
    """Admin: create recurring bookings for multiple day/slot combinations."""
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        dog_id          = data.get('dog_id')
        user_id         = data.get('user_id')
        start_str       = data.get('start_date', '')
        end_str         = data.get('end_date', '')
        day_slots       = data.get('day_slots', [])
        service_type_id = data.get('service_type_id')

        if not all([dog_id, user_id, start_str, end_str]):
            return jsonify(success=False, message="Missing required fields"), 400
        if not day_slots:
            return jsonify(success=False, message="Please select at least one day"), 400

        # Recurring bookings are walk-only. Reject any other service type so a
        # client mistake (or hand-crafted request) can't create recurring
        # drop-ins, which we don't support.
        if service_type_id:
            svc = db.session.get(ServiceType, service_type_id)
            if not svc or svc.slug != ServiceType.WALK:
                return jsonify(success=False, message="Recurring bookings are only available for walks"), 400

        for entry in day_slots:
            if entry.get('day') not in range(5):
                return jsonify(success=False, message="Invalid day"), 400
            if entry.get('slot') not in ('Morning', 'Afternoon'):
                return jsonify(success=False, message="Invalid slot"), 400

        from datetime import date as date_type
        from datetime import timedelta
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

        dog = db.session.get(Dog, dog_id)
        if not dog:
            return jsonify(success=False, message="Dog not found"), 404

        default_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
        if not default_service:
            return jsonify(success=False, message="No service type available"), 400

        # Generate (date, slot) pairs — walk every calendar day, emit entries whose weekday matches
        seen = set()
        target_pairs = []
        current = start_date
        while current <= end_date:
            wday = current.weekday()
            for entry in day_slots:
                if entry['day'] == wday:
                    key = (current, entry['slot'])
                    if key not in seen:
                        seen.add(key)
                        target_pairs.append(key)
            current += timedelta(days=1)

        if not target_pairs:
            return jsonify(success=False, message="No valid dates in that range"), 400

        active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
        confirmed = requested = waitlisted = skipped = 0
        # One batch_id ties together every booking in this recurring series so
        # the activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
        batch_id = uuid.uuid4().hex
        # Grouped notifications (§9.3/§9.4): one bell entry per (recipient, kind)
        # instead of one per booking. Admin acting on behalf → actor-prefixed
        # wording, unless the admin happens to own this dog.
        batch = NotificationBatch(actor_id=current_user.id)
        admin_first  = current_user.firstname or 'Admin'
        client_actor = None if current_user.id == int(user_id) else admin_first

        for d, slot in target_pairs:
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

            try:
                booking, auto_confirmed = create_booking(
                    dog=dog, user_id=user_id, date=d, slot=slot,
                    service=default_service, actor_id=current_user.id, batch_id=batch_id,
                    admin_override=True, created_by_id=current_user.id,
                )
            except CapacityError:
                skipped += 1
                continue

            if booking.status == 'confirmed':
                confirmed += 1
                walker = booking.walker
                walker_first = walker.user.firstname if walker and walker.user else None
                batch.add(user_id, 'booking_confirmed', actor_first=client_actor,
                          dog_name=dog.name, slot=slot, date=d, walker_name=walker_first)
                if walker and walker.user_id != current_user.id:
                    batch.add(walker.user_id, 'walker_assigned',
                              dog_name=dog.name, slot=slot, date=d)
            elif booking.status == 'waitlisted':
                waitlisted += 1
                batch.add(user_id, 'booking_waitlisted', actor_first=client_actor,
                          dog_name=dog.name, slot=slot, date=d)
            else:
                requested += 1
                batch.add(user_id, 'booking_requested', actor_first=client_actor,
                          dog_name=dog.name, slot=slot, date=d)

        db.session.flush()  # persist bookings before emitting notifications
        batch.flush()
        db.session.commit()
        return jsonify(success=True, confirmed=confirmed, requested=requested, waitlisted=waitlisted, skipped=skipped)

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in admin recurring_for_dog: {e}")
        return jsonify(success=False, message="Server error"), 500


@admin_bp.route("/dogs/<int:dog_id>/upcoming-bookings")
@login_required
@admin_required
def dog_upcoming_bookings(dog_id):
    """AJAX: paginated upcoming (and future) bookings for a dog."""
    from datetime import date as date_type
    dog = db.session.get(Dog, dog_id)
    if not dog:
        return jsonify(success=False, message="Dog not found"), 404

    from_str = request.args.get('from', '')
    try:
        from_date = date_type.fromisoformat(from_str) if from_str else date_type.today()
    except ValueError:
        from_date = date_type.today()

    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 10

    query = (
        Booking.query
        .filter(
            Booking.dog_id == dog_id,
            Booking.date >= from_date,
            Booking.status.notin_(['cancelled', 'rejected', 'completed']),
        )
        .order_by(Booking.date, Booking.slot)
    )

    # Optional service-type filter by slug (e.g. 'group-walk' / 'drop-in').
    service = request.args.get('service', '')
    if service:
        st = ServiceType.query.filter_by(slug=service).first()
        if st:
            query = query.filter(Booking.service_type_id == st.id)

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    bookings = query.offset((page - 1) * per_page).limit(per_page).all()

    rows = []
    for b in bookings:
        walker_name = ''
        if b.walker and b.walker.user:
            walker_name = b.walker.user.firstname
        rows.append({
            # "Thu, 5 Jun" — matches the cancel modal's formatBcDate output.
            'date': b.date.strftime('%a, %-d %b'),
            'slot': b.slot,
            'status': b.status,
            'walker': walker_name,
            'service': b.service_type.name if b.service_type else '',
            'service_slug': b.service_type.slug if b.service_type else '',
        })

    return jsonify(
        success=True,
        bookings=rows,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@admin_bp.route("/dogs/<int:dog_id>/cancel-preview")
@login_required
@admin_required
def dog_cancel_preview(dog_id):
    """AJAX: preview bookings that would be cancelled in a date range (no writes)."""
    from datetime import date as date_type
    dog = db.session.get(Dog, dog_id)
    if not dog:
        return jsonify(success=False, message="Dog not found"), 404

    try:
        start = date_type.fromisoformat(request.args.get('start', ''))
        end   = date_type.fromisoformat(request.args.get('end', ''))
    except (ValueError, TypeError):
        return jsonify(success=False, message="Invalid dates"), 400

    if end < start:
        return jsonify(success=False, message="End date must be on or after start date"), 400
    if (end - start).days > 365:
        return jsonify(success=False, message="Range cannot exceed one year"), 400

    # Optional slot filter — 0 or 2 valid values = no filter (Both). 1 = narrow.
    slot_filter = [s for s in request.args.getlist('slots') if s in ('Morning', 'Afternoon')]
    # Optional day-of-week filter — empty set = no filter (matches slot semantic).
    day_filter = _parse_day_filter(request.args.getlist('days'))
    # Optional service filter — 'all' (or missing) = every service type.
    service_filter = request.args.get('service', 'all')

    q = (
        Booking.query
        .options(joinedload(Booking.service_type))
        .filter(
            Booking.dog_id == dog_id,
            Booking.date >= start,
            Booking.date <= end,
            Booking.status.notin_(['cancelled', 'rejected', 'completed']),
        )
    )
    if len(slot_filter) == 1:
        q = q.filter(Booking.slot == slot_filter[0])
    if service_filter and service_filter != 'all':
        q = q.join(ServiceType).filter(ServiceType.slug == service_filter)
    bookings = q.order_by(Booking.date, Booking.slot).all()

    # Filter weekdays in Python — avoids the Postgres DOW-offset gotcha
    # (see CLAUDE.md), and the per-dog dataset is small.
    if day_filter:
        bookings = [b for b in bookings if b.date.weekday() in day_filter]

    # How many fall inside the notice window — these bill by default unless the
    # admin waives. The UI uses late_count to show/hide the late-fee checkbox.
    today = datetime.now(timezone.utc).date()
    late_count = sum(1 for b in bookings if is_late_cancellation(b, today))

    # The preview only needs to confirm the admin picked the right dates/days —
    # the range can span hundreds of walks, so cap the serialised list at 10.
    # `count` stays the true total (the UI shows "… preview of the first 10").
    PREVIEW_CAP = 10
    return jsonify(
        success=True,
        count=len(bookings),
        late_count=late_count,
        bookings=[{
            'date': b.date.isoformat(),
            'slot': b.slot,
            'status': b.status,
            'late': is_late_cancellation(b, today),
        } for b in bookings[:PREVIEW_CAP]],
    )


@admin_bp.route("/dogs/<int:dog_id>/bulk-cancel", methods=["POST"])
@login_required
@admin_required
def dog_bulk_cancel(dog_id):
    """Admin: cancel all active bookings for a dog within a date range."""
    from datetime import date as date_type
    dog = db.session.get(Dog, dog_id)
    if not dog:
        return jsonify(success=False, message="Dog not found"), 404

    data = request.get_json(silent=True) or {}
    try:
        start = date_type.fromisoformat(data.get('start', ''))
        end   = date_type.fromisoformat(data.get('end', ''))
    except (ValueError, TypeError):
        return jsonify(success=False, message="Invalid dates"), 400

    if end < start:
        return jsonify(success=False, message="End date must be on or after start date"), 400
    if (end - start).days > 365:
        return jsonify(success=False, message="Range cannot exceed one year"), 400

    # Optional slot filter — 0 or 2 valid values = no filter (Both). 1 = narrow.
    slots_raw   = data.get('slots') or []
    slot_filter = [s for s in slots_raw if s in ('Morning', 'Afternoon')]
    # Optional day-of-week filter — empty set = no filter (matches slot semantic).
    day_filter = _parse_day_filter(data.get('days') or [])
    # Optional service filter — 'all' (or missing) = every service type. Must
    # match the preview's filter exactly so the count the admin saw is the
    # count cancelled.
    service_filter = data.get('service', 'all')

    q = (
        Booking.query
        .filter(
            Booking.dog_id == dog_id,
            Booking.date >= start,
            Booking.date <= end,
            Booking.status.notin_(['cancelled', 'rejected', 'completed']),
        )
    )
    if len(slot_filter) == 1:
        q = q.filter(Booking.slot == slot_filter[0])
    if service_filter and service_filter != 'all':
        q = q.join(ServiceType).filter(ServiceType.slug == service_filter)
    bookings = q.order_by(Booking.date).all()

    # Filter weekdays in Python (see preview route for rationale).
    if day_filter:
        bookings = [b for b in bookings if b.date.weekday() in day_filter]

    if not bookings:
        return jsonify(success=True, cancelled_count=0)

    n = len(bookings)

    # Capture walker IDs before bulk_transition clears them (§7.4).
    # Maps walker.user_id → list of (slot, date, svc_label) for notifications.
    walker_payloads = {}
    for b in bookings:
        if b.walker_id and b.walker and b.walker.user_id:
            wuid = b.walker.user_id
            if wuid != current_user.id:
                b_svc = 'drop-in' if b.service_type and b.service_type.slug == ServiceType.DROP_IN else 'walk'
                walker_payloads.setdefault(wuid, []).append(
                    dict(dog_name=dog.name, slot=b.slot, date=b.date, svc_label=b_svc)
                )

    # One batch_id ties together every cancellation in this bulk-cancel action
    # so the activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
    batch_id = uuid.uuid4().hex
    # Late-cancel billing (admin): bookings inside the notice window bill by
    # default unless `waive_late_fee` is set; bookings outside the window are
    # never late so leave bill_cancellation=None. Set the flag only on the late
    # subset — an explicit True on a non-late row would wrongly bill it.
    today = datetime.now(timezone.utc).date()
    waive = bool(data.get('waive_late_fee'))
    late, not_late = [], []
    for b in bookings:
        (late if is_late_cancellation(b, today) else not_late).append(b)
    if late:
        bulk_transition(late, 'cancelled', actor_id=current_user.id,
                        walker_id=None, cancelled_by='admin', batch_id=batch_id,
                        bill_cancellation=(not waive))
    if not_late:
        bulk_transition(not_late, 'cancelled', actor_id=current_user.id,
                        walker_id=None, cancelled_by='admin', batch_id=batch_id)

    # Notify dog owners (excluding admins) and assigned walkers (§7.4) —
    # one grouped notice each.
    batch = NotificationBatch(actor_id=current_user.id)
    owners = DogOwner.query.filter_by(dog_id=dog_id).all()
    for o in owners:
        owner_user = db.session.get(User, o.user_id)
        if owner_user and not owner_user.is_admin:
            for b in bookings:
                b_svc = ('drop-in' if b.service_type
                         and b.service_type.slug == ServiceType.DROP_IN else 'walk')
                batch.add(owner_user.id, 'booking_cancelled',
                          dog_name=dog.name, slot=b.slot, date=b.date, svc_label=b_svc)
    for wuid, payloads in walker_payloads.items():
        for p in payloads:
            batch.add(wuid, 'booking_cancelled', **p)
    batch.flush()

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logging.error(f"Bulk cancel error for dog {dog_id}: {e}")
        return jsonify(success=False, message="Failed to cancel bookings"), 500

    return jsonify(success=True, cancelled_count=n)
