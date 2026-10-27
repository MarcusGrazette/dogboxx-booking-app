"""
Client booking routes — creation (single, both-slots, drop-in, recurring),
cancellation, pause-walks, calendar JSON, and booking notes.
"""

from flask import current_app, request, redirect, render_template, flash, url_for, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models import User, Dog, Booking, ServiceType, Walker, Closure
from app import db, limiter
from app.utils.db_error_handler import DBErrorHandler
from app.utils.booking_access import get_accessible_dogs, get_accessible_dog_ids, user_can_access_booking
from app.capacity import eligible_walker_clause, is_date_closed, MAX_RECURRING_SERIES
from app.forms import BookingForm
import logging
import uuid
from datetime import datetime, timezone, timedelta, date as date_type

from app.blueprints.client import client_bp
from app.utils.booking_notify import notify_cancelled, notify_created
from app.utils.booking_status import (
    transition_booking, bulk_transition,
    InvalidTransitionError,
)
from app.utils.invoicing import (
    is_late_cancellation, bill_cancellation_for, group_by_bill_cancellation,
)
from app.utils.dates import local_today
from app.utils.decorators import has_client_access
from app.services.booking_service import create_booking, CapacityError


def _is_same_day(booking_date):
    """True if booking_date is today (server UTC). Same-day bookings skip
    auto-assignment — walker schedules are planned in advance, so Lydia
    reviews these manually."""
    return booking_date == datetime.now(timezone.utc).date()



def _resolve_dog(user_dogs, requested_id):
    """Return the Dog to book for.

    If requested_id is provided and belongs to this user, return that dog.
    Otherwise fall back to the first dog (single-dog accounts / legacy callers).
    Raises ValueError if requested_id is provided but not accessible.
    """
    if requested_id:
        try:
            requested_id = int(requested_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid dog selection.")
        dog = next((d for d in user_dogs if d.id == requested_id), None)
        if dog is None:
            raise ValueError("Dog not found on your account.")
        return dog
    if len(user_dogs) > 1:
        raise ValueError("Please select a dog.")
    return user_dogs[0]


@client_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """Render the home page for clients."""
    # Admins land on /admin unless they've explicitly switched to client view
    if current_user.is_admin and session.get('active_view') != 'client':
        return redirect(url_for('admin.index'))
    if current_user.role == 'walker':
        # Dual-role walker in client view: let through. Otherwise send to walker home.
        if current_user.client is None or session.get('active_view') != 'client':
            return redirect(url_for('walker.pickups'))

    user = User.query.options(
        joinedload(User.client)
    ).filter_by(id=current_user.id).first()

    # The user's dogs (primary or co-owned), minus deleted dogs
    user_dogs = get_accessible_dogs(current_user.id)

    # Return all non-cancelled bookings (past and upcoming) for date-filter support
    today = datetime.now(timezone.utc).date()
    _index_dog_ids = [d.id for d in user_dogs]
    upcoming_bookings_query = Booking.query.options(
        joinedload(Booking.walker).joinedload(Walker.user),
        joinedload(Booking.service_type),
    ).filter(
        Booking.dog_id.in_(_index_dog_ids),
        Booking.status.notin_(['cancelled', 'rejected']),
    ).order_by(Booking.date.asc())

    upcoming_bookings = list(upcoming_bookings_query)
    for b in upcoming_bookings:
        if b.date:
            b.date_display = b.date.strftime("%a %d %b")
        else:
            b.date_display = None
        b.is_drop_in = b.service_type and b.service_type.slug == ServiceType.DROP_IN
        b.is_past = (b.date < today) if b.date else False

    form = BookingForm()
    if form.validate_on_submit():
        booking_date = form.date.data
        booking_slot = form.slot.data

        today = datetime.now(timezone.utc).date()
        errors = []

        if booking_date < today:
            errors.append("Booking date cannot be in the past.")

        # Validate slot against allowed enum values
        if booking_slot not in ("Morning", "Afternoon"):
            errors.append("Invalid booking slot selected.")

         # Ensure the user has at least one dog to book
        if not user or not user_dogs:
            errors.append("No dog found on your account. Please add a dog before booking.")

        if not errors:
            try:
                selected_dog = _resolve_dog(user_dogs, request.form.get('dog_id'))
                dog_id = selected_dog.id
            except ValueError as e:
                errors.append(str(e))

        if not errors:
            # Prevent duplicate booking: same dog + date + slot (any service type)
            active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
            walk_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
            existing = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == booking_date,
                Booking.slot == booking_slot,
                Booking.status.in_(active_statuses),
            ).first()
            if existing:
                errors.append("This dog already has a booking for that slot on that date.")

            # Cap at 2 bookings per dog per day (one per slot)
            day_count = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == booking_date,
                Booking.status.in_(active_statuses)
            ).count()
            if day_count >= 2:
                errors.append("This dog already has two bookings on that date (one per slot is the maximum).")

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            # Use context manager for error handling
            with DBErrorHandler(
                flash_message=True,
                custom_error_messages={
                    IntegrityError: "Could not create booking due to a conflict. You might already have a booking at this time.",
                    OperationalError: "Our booking system is temporarily unavailable. Please try again later."
                }
            ):
                if not walk_service:
                    flash("No service type available. Please contact support.", "error")
                    return redirect(url_for("client.index"))
                default_service = walk_service

                same_day = _is_same_day(booking_date)
                dog = db.session.get(Dog, dog_id)
                batch_id = uuid.uuid4().hex
                try:
                    new_booking, auto_confirmed = create_booking(
                        dog=dog, user_id=user.id, date=booking_date, slot=booking_slot,
                        service=default_service, actor_id=current_user.id, batch_id=batch_id,
                        same_day=same_day,
                    )
                except CapacityError as e:
                    flash(str(e), "warning")
                    return redirect(url_for("client.index"))

                booking_status = new_booking.status
                notify_created([new_booking], actor=current_user, by_admin=False,
                               same_day=same_day)
                db.session.commit()
                if booking_status == 'waitlisted':
                    flash(f"All slots are currently full for {booking_slot} on {booking_date.strftime('%d %b')}. "
                          f"You've been added to the waitlist — we'll let you know if a spot opens up.", "info")
                elif auto_confirmed:
                    flash(f"Booking confirmed for {booking_slot} on {booking_date.strftime('%d %b')}!", "success")
                elif same_day:
                    flash(f"Same-day request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly.", "info")
                else:
                    flash("Booking request submitted — we'll confirm it shortly.", "success")
                return redirect(url_for("client.index"))

    has_drop_in_walkers = Walker.query.join(User).filter(
        Walker.does_drop_ins == True,
        eligible_walker_clause(),
    ).first() is not None

    return render_template("index.html", user=user, client=user.client, dogs=user_dogs,
                           bookings=upcoming_bookings, form=form,
                           has_drop_in_walkers=has_drop_in_walkers,
                           today=today) # type: ignore


@client_bp.route("/book", methods=["POST"])
@login_required
def book():
    """AJAX single booking endpoint — returns JSON, no page reload.

    Accepts JSON body: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon" }
    Returns: { "success": bool, "message": str, "booking": {...} }
    """
    data = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()
    booking_slot     = data.get('slot', '').strip()

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not booking_date_str or not booking_slot:
        return jsonify({'success': False, 'message': 'Date and slot are required.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today   = datetime.now(timezone.utc).date()

    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400
    if booking_slot not in ('Morning', 'Afternoon'):
        return jsonify({'success': False, 'message': 'Invalid slot selected.'}), 400

    user_dogs = get_accessible_dogs(current_user.id)
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account. Please add a dog before booking.'}), 400

    try:
        dog = _resolve_dog(user_dogs, data.get('dog_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    dog_id = dog.id

    # ── Service type + duplicate / cap checks ────────────────────────────────
    default_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
    if not default_service:
        return jsonify({'success': False, 'message': 'No service type available. Please contact support.'}), 500

    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    existing = Booking.query.filter(
        Booking.dog_id == dog_id,
        Booking.date   == booking_date,
        Booking.slot   == booking_slot,
        Booking.status.in_(active_statuses),
    ).first()
    if existing:
        svc_label = existing.service_type.name.lower() if existing.service_type else 'booking'
        return jsonify({'success': False, 'message': f'{dog.name} already has a {svc_label} booked for that slot.'}), 409

    day_count = Booking.query.filter(
        Booking.dog_id == dog_id,
        Booking.date   == booking_date,
        Booking.status.in_(active_statuses),
    ).count()
    if day_count >= 2:
        return jsonify({'success': False, 'message': 'This dog already has two bookings on that date.'}), 409

    # ── Create + auto-assign ─────────────────────────────────────────────────
    try:
        same_day = _is_same_day(booking_date)
        closed, close_msg = is_date_closed(booking_date)
        if closed:
            return jsonify({'success': False, 'message': close_msg}), 409

        batch_id = uuid.uuid4().hex
        try:
            new_booking, auto_confirmed = create_booking(
                dog=dog, user_id=current_user.id, date=booking_date, slot=booking_slot,
                service=default_service, actor_id=current_user.id, batch_id=batch_id,
                same_day=same_day,
            )
        except CapacityError as e:
            return jsonify({'success': False, 'message': str(e)}), 409

        booking_status = new_booking.status
        notify_created([new_booking], actor=current_user, by_admin=False,
                       same_day=same_day)

        if booking_status == 'waitlisted':
            message = (f"All slots are full — you've been added to the waitlist "
                       f"for {booking_slot} on {booking_date.strftime('%d %b')}.")
        elif auto_confirmed:
            booking_status = 'confirmed'
            message = f"Booking confirmed for {booking_slot} on {booking_date.strftime('%d %b')}!"
        else:
            message = (f"Same-day request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
                       if same_day else "Booking request submitted — we'll confirm it shortly.")

        db.session.commit()

        walker_name = None
        if new_booking.walker_id and new_booking.walker:
            walker_name = new_booking.walker.user.firstname

        has_pickup_notes = bool(dog and dog.pickup_instructions)

        return jsonify({
            'success': True,
            'status':  booking_status,
            'message': message,
            'booking': {
                'id':               new_booking.id,
                'date_display':     booking_date.strftime('%a %-d %b'),
                'date_iso':         booking_date.isoformat(),
                'slot':             booking_slot,
                'status':           new_booking.status,
                'dog_id':           dog_id,
                'walker_name':      walker_name,
                'has_pickup_notes': has_pickup_notes,
            },
        })

    except IntegrityError:
        # Lost a concurrent-duplicate race (two co-owners / double-tapped Book):
        # the partial unique index on (dog_id, date, slot) rejected the second
        # write. Return the same graceful 409 the pre-check uses instead of a 500
        # (SECURITY_REVIEW.md #2).
        db.session.rollback()
        return jsonify({'success': False,
                        'message': f'{dog.name} already has a booking for that slot.'}), 409
    except Exception as e:
        db.session.rollback()
        logging.exception(f'AJAX booking error for user {current_user.id}: {e}')
        return jsonify({'success': False, 'message': 'An error occurred. Please try again.'}), 500


@client_bp.route("/book_both", methods=["POST"])
@login_required
def book_both():
    """AJAX endpoint: request both Morning and Afternoon for a single date.

    Accepts JSON: { "date": "YYYY-MM-DD" }
    Each slot is booked independently — one can be requested while the other
    is waitlisted if capacity is tight. Returns both results.
    """
    data             = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()

    if not booking_date_str:
        return jsonify({'success': False, 'message': 'Date is required.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today    = datetime.now(timezone.utc).date()
    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400

    user_dogs = get_accessible_dogs(current_user.id)
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account.'}), 400

    try:
        dog = _resolve_dog(user_dogs, data.get('dog_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    dog_id = dog.id

    default_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
    if not default_service:
        return jsonify({'success': False, 'message': 'No service type available.'}), 500

    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    same_day = _is_same_day(booking_date)
    closed, close_msg = is_date_closed(booking_date)
    if closed:
        return jsonify({'success': False, 'message': close_msg}), 409

    # One batch_id ties together both slots of this book-both action so the
    # activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
    batch_id     = uuid.uuid4().hex
    final_created = []
    skipped       = []

    for slot in ('Morning', 'Afternoon'):
        # Skip if any active booking already exists for this slot (any service type)
        if Booking.query.filter(
            Booking.dog_id == dog_id,
            Booking.date   == booking_date,
            Booking.slot   == slot,
            Booking.status.in_(active_statuses),
        ).first():
            skipped.append(slot)
            continue

        try:
            b, auto_confirmed = create_booking(
                dog=dog, user_id=current_user.id, date=booking_date, slot=slot,
                service=default_service, actor_id=current_user.id, batch_id=batch_id,
                same_day=same_day,
            )
        except CapacityError:
            skipped.append(slot)
            continue
        except IntegrityError:
            # Concurrent-duplicate race. create_booking() flushes the INSERT to
            # populate booking.id for the audit row, so the (dog_id, date, slot)
            # partial unique index rejects it HERE — not at the commit far below,
            # which is the only place this used to be caught. The advisory lock
            # is per-service while the index is cross-service (dog, date, slot),
            # so a walk racing a drop-in still collides.
            #
            # Unlike CapacityError we can't skip this slot and carry on: the
            # rollback also discards the other slot's booking, so continuing
            # would build notifications for a row that no longer exists. Abort
            # the whole request with one 409 and let the client reload. (The
            # per-slot alternative is a savepoint per slot; deliberately not
            # done — see the PR 1 notes on keeping savepoints out of this
            # codebase.)
            db.session.rollback()
            return jsonify({'success': False,
                            'message': f'{dog.name} already has a booking for one of those slots.'}), 409

        final_created.append((slot, b.status, b))

    if not final_created:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'No new bookings created — slots may already be booked.'}), 409

    notify_created([b for _, _, b in final_created], actor=current_user,
                   by_admin=False, same_day=same_day)

    try:
        db.session.commit()
    except IntegrityError:
        # Concurrent-duplicate race on one of the two slots — unique index
        # rejected it. Graceful 409 instead of a 500 (SECURITY_REVIEW.md #2).
        db.session.rollback()
        return jsonify({'success': False,
                        'message': f'{dog.name} already has a booking for one of those slots.'}), 409
    created = final_created

    has_pickup_notes = bool(dog and dog.pickup_instructions)

    # Build response
    booking_payload = []
    for slot, status, b in created:
        booking_payload.append({
            'id':               b.id,
            'date_display':     booking_date.strftime('%a %-d %b'),
            'date_iso':         booking_date.isoformat(),
            'slot':             slot,
            'status':           b.status,
            'dog_id':           dog_id,
            'walker_name':      b.walker.user.firstname if b.walker_id and b.walker else None,
            'has_pickup_notes': has_pickup_notes,
        })

    parts = []
    for slot, status, _ in created:
        label = 'AM' if slot == 'Morning' else 'PM'
        word = 'waitlisted' if status == 'waitlisted' else ('confirmed' if status == 'confirmed' else 'requested')
        parts.append(f'{label} {word}')
    if skipped:
        parts.append(f'{", ".join(skipped)} skipped (already booked)')

    return jsonify({
        'success':  True,
        'bookings': booking_payload,
        'message':  ', '.join(parts) + '.',
    })


@client_bp.route("/book_drop_in", methods=["POST"])
@login_required
def book_drop_in():
    """AJAX endpoint: request a drop-in visit for a given date + slot.

    Accepts JSON: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon" }
    Returns JSON response (success/failure + booking info).
    """
    data             = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()
    booking_slot     = data.get('slot', '').strip()

    if not booking_date_str:
        return jsonify({'success': False, 'message': 'Date is required.'}), 400
    if booking_slot not in ('Morning', 'Afternoon'):
        return jsonify({'success': False, 'message': 'Invalid slot. Choose Morning or Afternoon.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today    = datetime.now(timezone.utc).date()
    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400

    user_dogs = get_accessible_dogs(current_user.id)
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account.'}), 400

    try:
        dog = _resolve_dog(user_dogs, data.get('dog_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    dog_id = dog.id

    drop_in_service = ServiceType.query.filter_by(slug=ServiceType.DROP_IN, active=True).first()
    if not drop_in_service:
        return jsonify({'success': False, 'message': 'Drop-in service is not currently available.'}), 503

    # Prevent duplicate (any service type for this slot)
    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    existing = Booking.query.filter(
        Booking.dog_id == dog_id,
        Booking.date   == booking_date,
        Booking.slot   == booking_slot,
        Booking.status.in_(active_statuses),
    ).first()
    if existing:
        svc_label = existing.service_type.name.lower() if existing.service_type else 'booking'
        return jsonify({'success': False, 'message': f'{dog.name} already has a {svc_label} booked for that slot.'}), 409

    same_day = _is_same_day(booking_date)
    closed, close_msg = is_date_closed(booking_date)
    if closed:
        return jsonify({'success': False, 'message': close_msg}), 409

    batch_id = uuid.uuid4().hex
    try:
        new_booking, _ = create_booking(
            dog=dog, user_id=current_user.id, date=booking_date, slot=booking_slot,
            service=drop_in_service, actor_id=current_user.id, batch_id=batch_id,
            same_day=same_day, auto_confirm=False,
        )
    except CapacityError as e:
        return jsonify({'success': False, 'message': str(e)}), 409
    except IntegrityError:
        # Concurrent-duplicate race. create_booking() flushes the INSERT to
        # populate booking.id for the audit row, so the unique index rejects it
        # here rather than at the commit below — the commit-only handler that
        # used to be the sole guard never saw it, and this 500'd. A drop-in
        # racing a walk is the realistic case: the advisory lock is per-service,
        # the index is cross-service (dog, date, slot).
        db.session.rollback()
        return jsonify({'success': False,
                        'message': f'{dog.name} already has a booking for that slot.'}), 409

    booking_status = new_booking.status
    try:
        db.session.commit()
    except IntegrityError:
        # Backstop for a collision that somehow survives to commit time.
        db.session.rollback()
        return jsonify({'success': False,
                        'message': f'{dog.name} already has a booking for that slot.'}), 409

    notify_created([new_booking], actor=current_user, by_admin=False,
                   same_day=same_day)
    db.session.commit()

    if same_day:
        message = f"Same-day drop-in request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
    elif booking_status == 'waitlisted':
        message = (f"All drop-in slots are full for {booking_slot} on "
                   f"{booking_date.strftime('%d %b')}. You've been added to the waitlist.")
    else:
        message = "Drop-in request submitted — we'll confirm shortly."

    return jsonify({
        'success':  True,
        'status':   booking_status,
        'message':  message,
        'booking':  {
            'id':           new_booking.id,
            'dog_id':       dog_id,
            'date_display': booking_date.strftime('%a %-d %b'),
            'date_iso':     booking_date.isoformat(),
            'slot':         booking_slot,
            'status':       booking_status,
            'is_drop_in':   True,
        },
    })


@client_bp.route("/pause-walks/preview")
@login_required
def pause_walks_preview():
    """Return bookings that would be cancelled in a date range (no writes)."""
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403
    try:
        start = date_type.fromisoformat(request.args.get('start', ''))
        end   = date_type.fromisoformat(request.args.get('end', ''))
    except (ValueError, TypeError):
        return jsonify(success=False, error="Invalid dates"), 400

    today = local_today()
    if start <= today:
        return jsonify(success=False, error="Start date must be in the future"), 400
    if end < start:
        return jsonify(success=False, error="End date must be after start date"), 400
    if (end - start).days > 365:
        return jsonify(success=False, error="Range cannot exceed one year"), 400

    # Optional slot filter — accepts repeated ?slots=Morning&slots=Afternoon.
    # 0 or 2 valid values = no filter (Both). Exactly 1 narrows to that slot.
    slot_filter = [s for s in request.args.getlist('slots') if s in ('Morning', 'Afternoon')]

    dog_ids = get_accessible_dog_ids(current_user.id)
    q = Booking.query.filter(
        Booking.dog_id.in_(dog_ids),
        Booking.date >= start,
        Booking.date <= end,
        Booking.status.notin_(['cancelled', 'rejected', 'completed']),
    )
    if len(slot_filter) == 1:
        q = q.filter(Booking.slot == slot_filter[0])
    bookings = q.order_by(Booking.date, Booking.slot).all()

    return jsonify(
        success=True,
        count=len(bookings),
        bookings=[{
            'date': b.date.strftime('%-d %b'),
            'slot': b.slot,
            'dog':  b.dog.name if b.dog else '',
        } for b in bookings],
    )


@client_bp.route("/pause-walks", methods=["POST"])
@login_required
def pause_walks():
    """Cancel all active bookings for the client's dogs within a date range."""
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403
    try:
        data  = request.get_json(silent=True) or {}
        start = date_type.fromisoformat(data.get('start', ''))
        end   = date_type.fromisoformat(data.get('end', ''))
    except (ValueError, TypeError):
        return jsonify(success=False, error="Invalid dates"), 400

    today = local_today()
    if start <= today:
        return jsonify(success=False, error="Start date must be in the future"), 400
    if end < start:
        return jsonify(success=False, error="End date must be after start date"), 400
    if (end - start).days > 365:
        return jsonify(success=False, error="Range cannot exceed one year"), 400

    # Optional slot filter — 0 or 2 valid values = no filter (Both). 1 = narrow.
    slots_raw   = data.get('slots') or []
    slot_filter = [s for s in slots_raw if s in ('Morning', 'Afternoon')]

    dog_ids = get_accessible_dog_ids(current_user.id)
    q = Booking.query.filter(
        Booking.dog_id.in_(dog_ids),
        Booking.date >= start,
        Booking.date <= end,
        Booking.status.notin_(['cancelled', 'rejected', 'completed']),
    )
    if len(slot_filter) == 1:
        q = q.filter(Booking.slot == slot_filter[0])
    bookings = q.order_by(Booking.date).all()

    if not bookings:
        return jsonify(success=True, cancelled_count=0)

    n          = len(bookings)
    # Queued in the same transaction as the cancel; nothing commits until the end.
    notify_cancelled(bookings, actor=current_user, by_admin=False)

    # One batch_id ties together every cancellation in this pause action so the
    # activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
    batch_id = uuid.uuid4().hex
    # Persist an explicit bill/don't-bill decision per row (see
    # bill_cancellation_for): late confirmed walks bill, anything never
    # confirmed or outside the notice window doesn't. bulk_transition takes
    # one value per call, hence the grouping.
    buckets = group_by_bill_cancellation(bookings, cancelled_by='client', today=today)
    for bill, group in buckets.items():
        bulk_transition(group, 'cancelled', actor_id=current_user.id,
                        walker_id=None, cancelled_by='client', batch_id=batch_id,
                        bill_cancellation=bill)

    db.session.commit()
    return jsonify(success=True, cancelled_count=n)


@client_bp.route("/cancel_booking", methods=["POST"])
@login_required
def cancel_booking():
    """Cancel a booking.

    Authorization is handled below by user_can_access_booking() — which
    allows any current dog co-owner or admins (not the creator as such).
    No early role gate (used to reject dual-role walkers incorrectly).
    """
    try:
        json_data = request.get_json(silent=True)
        data = request.form or (json_data if isinstance(json_data, dict) else {})
        booking_id = data.get("booking_id")
        if not booking_id:
            return jsonify(success=False, message="No booking ID provided"), 400

        # FOR UPDATE row lock, held until the commit/rollback below. The status
        # checks further down (idempotent repeat-cancel, allowed_from) read
        # booking.status — without the lock they read a snapshot a concurrent
        # assign_walker could overwrite between our read and our write (lost
        # update: the assign commits 'confirmed' over this cancel). With it, the
        # second of two racing requests blocks here and then sees the first's
        # committed status. No-op on SQLite.
        booking = db.session.get(Booking, booking_id, with_for_update=True,
                                 populate_existing=True)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404

        # Check authorization — any current dog owner, or admins
        if not user_can_access_booking(current_user, booking):
            return jsonify(success=False, message="You are not authorized to cancel this booking"), 403

        # Idempotent no-op: the client's intent (no walk) is already satisfied.
        # Re-running the cancel below would overwrite cancelled_at/cancelled_by/
        # bill_cancellation with the *replay's* values — for the legacy
        # bill_cancellation=None branch that shifts the notice-window arithmetic
        # away from the original cancellation, silently changing what's billed.
        if booking.status == 'cancelled':
            return jsonify(success=True, message="Booking already cancelled")

        is_admin_cancel = current_user.is_admin and booking.user_id != current_user.id

        # Reject client-initiated cancellation of a past booking — the UI only
        # hides the button, it doesn't block the request. Admin back-fill onto
        # past dates is deliberate elsewhere (book_for_dog), so only the
        # client-initiated branch is restricted here. London date, not UTC —
        # UTC is still "yesterday" for the first hour of a BST day.
        today = local_today()
        if not is_admin_cancel and booking.date < today:
            return jsonify(success=False, message="This booking is in the past and can't be cancelled"), 400

        # Late-cancel billing override (admin cancels only). When an admin cancels
        # a booking inside the notice window, bill by default and let them waive
        # via `waive_late_fee` — see app/utils/invoicing.py.
        admin_override = None
        if is_admin_cancel and is_late_cancellation(booking, today):
            form = request.form if request.form else (request.get_json(silent=True) or {})
            waive = str(form.get('waive_late_fee', '')).lower() in ('1', 'true', 'on', 'yes')
            admin_override = not waive

        # Decided now, while booking.status is still the pre-cancel status, and
        # persisted as an explicit True/False: never-confirmed bookings never
        # bill, the admin override wins for late confirmed ones, otherwise
        # client-and-late bills. See bill_cancellation_for().
        bill_cancellation = bill_cancellation_for(
            booking, cancelled_by='admin' if is_admin_cancel else 'client',
            today=today, admin_override=admin_override,
        )

        # Nothing commits until the end, so a rejected transition below rolls
        # these notifications back too.
        notify_cancelled([booking], actor=current_user, by_admin=is_admin_cancel)

        # transition_booking sets status, cancelled_at and logs the BSC row.
        # cancelled_by records who cancelled (admin acting on a client's booking
        # vs the client/owner themselves); walker_id=None unassigns.
        # allowed_from rejects a booking that isn't actually active any more
        # (e.g. two tabs racing to cancel/reject the same booking).
        transition_booking(booking, 'cancelled', actor_id=current_user.id,
                            cancelled_by='admin' if is_admin_cancel else 'client',
                            walker_id=None, bill_cancellation=bill_cancellation,
                            allowed_from={'requested', 'waitlisted', 'confirmed'})
        db.session.commit()
        return jsonify(success=True, message="Booking successfully cancelled")

    except InvalidTransitionError:
        db.session.rollback()
        return jsonify(success=False, message="This booking's status just changed — please refresh and try again"), 409
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error cancelling booking: {e}")
        return jsonify(success=False, message="Server error"), 500


@client_bp.route("/calendar_data/<int:year>/<int:month>")
@login_required
def calendar_data(year, month):
    """Return this client's bookings for a given month, for the booking calendar."""
    try:
        start_date = date_type(year, month, 1)
        end_date = date_type(year + 1, 1, 1) if month == 12 else date_type(year, month + 1, 1)
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    accessible_dog_ids = get_accessible_dog_ids(current_user.id)
    bookings = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.date >= start_date,
        Booking.date < end_date,
        Booking.status.notin_(['cancelled', 'rejected'])
    ).all()

    # Confirmed takes priority if multiple bookings on same day
    dates = {}
    for b in bookings:
        ds = b.date.strftime('%Y-%m-%d')
        if b.status == 'confirmed':
            dates[ds] = 'confirmed'
        elif ds not in dates:
            dates[ds] = 'pending'

    closures = Closure.query.filter(
        Closure.date >= start_date,
        Closure.date < end_date,
    ).all()
    closed_dates = [c.date.strftime('%Y-%m-%d') for c in closures]

    return jsonify(success=True, dates=dates, closed_dates=closed_dates)


@client_bp.route("/recurring_booking", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def recurring_booking():
    """Create a series of bookings from a start date, end date, slot and frequency.

    POST body (JSON):
        start_date  (str)  'YYYY-MM-DD' — must be tomorrow or later
        end_date    (str)  'YYYY-MM-DD' — max 1 year from start_date (client limit)
        slot        (str)  'Morning' or 'Afternoon'
        frequency   (str)  'daily' (weekdays only) or 'weekly'

    For each date in the range:
        - Skips weekends when frequency='daily'
        - Skips dates where this dog already has an active booking in that slot
        - Skips dates where the dog already has 2 bookings (one per slot limit)
        - Books as 'requested' if capacity available, 'waitlisted' if full

    Returns JSON: { success, created, waitlisted, skipped }

    Note: the 1-year cap is a client-facing safeguard, as is
    MAX_RECURRING_SERIES below (app.capacity — shared with
    /admin/recurring_for_dog).
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        start_str = data.get('start_date', '')
        end_str = data.get('end_date', '')
        slot = data.get('slot', '')
        frequency = data.get('frequency', '')

        if not all([start_str, end_str, slot, frequency]):
            return jsonify(success=False, message="Missing required fields"), 400

        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date format"), 400

        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=1)
        max_end = start_date + timedelta(days=365)

        if start_date < tomorrow:
            return jsonify(success=False, message="Start date must be in the future"), 400
        if end_date > max_end:
            return jsonify(success=False, message="End date must be within one year of the start date"), 400
        if end_date < start_date:
            return jsonify(success=False, message="End date must be after start date"), 400
        if slot not in ('Morning', 'Afternoon', 'Both'):
            return jsonify(success=False, message="Invalid slot"), 400
        if frequency not in ('daily', 'weekly'):
            return jsonify(success=False, message="Invalid frequency"), 400

        # Generate target dates
        target_dates = []
        delta = timedelta(days=1) if frequency == 'daily' else timedelta(weeks=1)
        current = start_date
        while current <= end_date:
            if frequency == 'daily' and current.weekday() >= 5:
                current += timedelta(days=1)
                continue  # Skip weekends for daily
            target_dates.append(current)
            current += delta

        if not target_dates:
            return jsonify(success=False, message="No valid dates in that range"), 400

        # Get dog
        user_dogs = get_accessible_dogs(current_user.id)
        if not user_dogs:
            return jsonify(success=False, message="No dog found on your account"), 400
        try:
            dog = _resolve_dog(user_dogs, data.get('dog_id'))
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400

        service_type_param = data.get('service_type', 'walk')
        is_drop_in = (service_type_param == ServiceType.DROP_IN)
        service_slug = ServiceType.DROP_IN if is_drop_in else ServiceType.WALK
        default_service = ServiceType.query.filter_by(slug=service_slug, active=True).first()
        if not default_service:
            return jsonify(success=False, message="No service type available"), 400

        # Drop-ins are single-slot only — reject 'Both' for drop-in
        if is_drop_in and slot == 'Both':
            return jsonify(success=False, message="Drop-ins cannot use the 'Both' slot"), 400

        active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
        confirmed = created = waitlisted = skipped = 0
        made = []  # committed bookings, for the notifications after the loop

        # One batch_id ties together every booking in this recurring series so
        # the activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
        batch_id = uuid.uuid4().hex

        slots_to_book = ['Morning', 'Afternoon'] if slot == 'Both' else [slot]

        # Each iteration below takes a transaction-scoped advisory lock
        # (capacity.py::acquire_booking_lock) — bounding total iterations here
        # bounds request latency and query volume even with per-iteration
        # commits releasing each lock quickly (see loop below).
        total_ops = len(target_dates) * len(slots_to_book)
        if total_ops > MAX_RECURRING_SERIES:
            # Deliberately no raw number in the message: MAX_RECURRING_SERIES
            # is a backend implementation detail (weekday-count x slots), not
            # something a client thinks in terms of on a date picker. This
            # should be unreachable via the UI anyway — the number was sized
            # to exactly cover the 1-year cap above at Both slots — so seeing
            # it at all implies a direct API call or a future UI change that
            # relaxed the 1-year cap without revisiting this one.
            return jsonify(success=False,
                           message="That date range creates too many bookings for one "
                                   "request — please choose a shorter range."), 400

        try:
            for d in target_dates:
                for s in slots_to_book:
                    existing = Booking.query.filter(
                        Booking.dog_id == dog.id,
                        Booking.date   == d,
                        Booking.slot   == s,
                        Booking.status.in_(active_statuses),
                    ).first()
                    if existing:
                        skipped += 1
                        continue

                    day_count = Booking.query.filter(
                        Booking.dog_id == dog.id,
                        Booking.date == d,
                        Booking.status.in_(active_statuses)
                    ).count()
                    if day_count >= 2:
                        skipped += 1
                        continue

                    try:
                        booking, auto_confirmed = create_booking(
                            dog=dog, user_id=current_user.id, date=d, slot=s,
                            service=default_service, actor_id=current_user.id, batch_id=batch_id,
                            auto_confirm=not is_drop_in,
                        )
                    except CapacityError:
                        db.session.rollback()
                        skipped += 1
                        continue
                    except IntegrityError:
                        # Concurrent-duplicate race on this specific date/slot (unique
                        # index on active (dog_id, date, slot)) — someone else booked
                        # it first. With per-booking commits this can no longer roll
                        # back the whole series, so treat it like a capacity conflict:
                        # skip this one slot and keep going.
                        db.session.rollback()
                        skipped += 1
                        continue

                    if booking.status == 'waitlisted':
                        waitlisted += 1
                    elif auto_confirmed:
                        confirmed += 1
                    else:
                        created += 1

                    # Commit per booking, not once at the end: the advisory lock
                    # create_booking() takes is transaction-scoped, so holding one
                    # open uncommitted transaction across the whole series would hold
                    # every lock in it simultaneously for the series' duration,
                    # blocking any other booking on those same (service, date, slot)
                    # combos. Committing here releases each lock within milliseconds
                    # instead. Read above, before this commit: a commit expires every
                    # object in the session, so reading booking.status after it would
                    # cost an extra re-fetch query for no benefit.
                    db.session.commit()
                    made.append(booking)
        except Exception as e:
            # An unexpected failure mid-series (a DB blip, a statement timeout).
            # The bookings committed so far stay, so still send their
            # notifications; the client sees them on their bookings page, and
            # retrying the same range skips them and creates the rest.
            db.session.rollback()
            logging.exception(f"Recurring booking series failed part-way: {e}")
            notify_created(made, actor=current_user, by_admin=False)
            db.session.commit()
            return jsonify(success=False,
                           message="Some walks couldn't be booked — please try again."), 500

        notify_created(made, actor=current_user, by_admin=False)
        db.session.commit()
        return jsonify(success=True, confirmed=confirmed, created=created, waitlisted=waitlisted, skipped=skipped)

    except IntegrityError:
        # Per-slot duplicate races are now caught inside the loop above (each
        # booking commits on its own) — this only catches an IntegrityError from
        # the final notification commit, which isn't expected but stays a
        # graceful 409 instead of a 500 if it ever happens (SECURITY_REVIEW.md #2).
        db.session.rollback()
        return jsonify(success=False,
                       message="Something changed while processing your bookings — please reload and check the results."), 409
    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error creating recurring bookings: {e}")
        return jsonify(success=False, message="Server error"), 500


@client_bp.route("/booking/<int:booking_id>/note", methods=["POST"])
@login_required
def update_booking_note(booking_id):
    """Save or clear the client note on a booking.

    Authorization is enforced via user_can_access_booking() below — admin
    or any current dog co-owner. No early role gate (used to
    reject dual-role walkers incorrectly).
    """
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return jsonify(success=False, message="Booking not found"), 404

    if not user_can_access_booking(current_user, booking):
        return jsonify(success=False, message="Not your booking"), 403

    data = request.get_json(silent=True) or {}
    note = (data.get('note') or '').strip()
    if len(note) > Booking.NOTE_MAX_LENGTH:
        return jsonify(success=False,
                       message=f"Note must be {Booking.NOTE_MAX_LENGTH} characters or fewer"), 400

    booking.client_notes = note or None
    db.session.commit()
    return jsonify(success=True, note=booking.client_notes)
