"""
Client booking routes — creation (single, both-slots, drop-in, recurring),
cancellation, pause-walks, calendar JSON, and booking notes.
"""

from flask import current_app, request, redirect, render_template, url_for, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from app.models import User, Booking, ServiceType, Walker, Closure
from app import db, limiter
from app.utils.booking_access import get_accessible_dogs, get_accessible_dog_ids, user_can_access_booking
from app.capacity import eligible_walker_clause, is_date_closed, MAX_RECURRING_SERIES
from app.forms import BookingForm
import logging
import uuid
from datetime import datetime, timedelta, date as date_type

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
from app.utils.pricing import SLOT_ORDER
from app.utils.decorators import has_client_access
from app.services.booking_service import create_booking, CapacityError


def _is_same_day(booking_date):
    """True if booking_date is today (London date). Same-day bookings skip
    auto-assignment — walker schedules are planned in advance, so Lydia
    reviews these manually."""
    return booking_date == local_today()



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


def _booking_sort_key(b):
    """Chronological, Morning before Afternoon on the same day — the order
    client-home.js's refreshList() keeps the list in."""
    return (b.date, SLOT_ORDER.get(b.slot, len(SLOT_ORDER)))


# 'modified' is a fossil label (never written); kept so the filter matches the
# statuses the duplicate index treats as active.
_ACTIVE_STATUSES = ('requested', 'confirmed', 'modified', 'waitlisted')


def _validate_booking_request(data, *, with_slot=True):
    """Input checks shared by /book, /book_drop_in and /book_both (no slot).

    Returns ((date, slot, dog), None), or (None, (json_response, status)).
    """
    def error(message):
        return None, (jsonify({'success': False, 'message': message}), 400)

    date_str = (data.get('date') or '').strip()
    slot = (data.get('slot') or '').strip() if with_slot else None
    if not date_str or (with_slot and not slot):
        return error('Date and slot are required.' if with_slot else 'Date is required.')
    try:
        booking_date = date_type.fromisoformat(date_str)
    except ValueError:
        return error('Invalid date format.')
    if booking_date < local_today():
        return error('Booking date cannot be in the past.')
    if with_slot and slot not in ('Morning', 'Afternoon'):
        return error('Invalid slot selected.')

    user_dogs = get_accessible_dogs(current_user.id)
    if not user_dogs:
        return error('No dog found on your account. Please add a dog before booking.')
    try:
        dog = _resolve_dog(user_dogs, data.get('dog_id'))
    except ValueError as e:
        return error(str(e))
    return (booking_date, slot, dog), None


def _create_booking_request(service_slug, *, auto_confirm, missing_service):
    """One booking from a /book or /book_drop_in JSON request: validate,
    refuse a taken slot or closed date, create, queue notifications and
    commit once — booking and notifications are saved together or not at all.

    missing_service: (message, status) to reply with if the service is off.
    Returns ((booking, status, auto_confirmed, same_day), None), or
    (None, (json_response, status)).
    """
    def error(message, code):
        return None, (jsonify({'success': False, 'message': message}), code)

    data = request.get_json(silent=True) or {}
    request_fields, invalid = _validate_booking_request(data)
    if invalid:
        return None, invalid
    booking_date, slot, dog = request_fields

    service = ServiceType.query.filter_by(slug=service_slug, active=True).first()
    if not service:
        return error(*missing_service)

    # Any service: the duplicate index is (dog, date, slot) across services.
    existing = Booking.query.filter(
        Booking.dog_id == dog.id,
        Booking.date   == booking_date,
        Booking.slot   == slot,
        Booking.status.in_(_ACTIVE_STATUSES),
    ).first()
    if existing:
        svc_label = existing.service_type.name.lower() if existing.service_type else 'booking'
        return error(f'{dog.name} already has a {svc_label} booked for that slot.', 409)

    # create_booking() lets same-day requests past its own closure check
    # (owner assigns manually), so this check is what stops them.
    closed, close_msg = is_date_closed(booking_date)
    if closed:
        return error(close_msg, 409)

    same_day = _is_same_day(booking_date)
    try:
        booking, auto_confirmed = create_booking(
            dog=dog, user_id=current_user.id, date=booking_date, slot=slot,
            service=service, actor_id=current_user.id, batch_id=uuid.uuid4().hex,
            same_day=same_day, auto_confirm=auto_confirm,
        )
        notify_created([booking], actor=current_user, by_admin=False, same_day=same_day)
        status = booking.status  # read before the commit expires it
        db.session.commit()
    except CapacityError as e:
        db.session.rollback()
        return error(str(e), 409)
    except IntegrityError:
        # Lost a concurrent-duplicate race (two co-owners / a double tap, or a
        # walk racing a drop-in — the advisory lock is per-service, the unique
        # index cross-service). create_booking() flushes, so this usually
        # fires there rather than at the commit; both are covered.
        db.session.rollback()
        return error(f'{dog.name} already has a booking for that slot.', 409)
    except Exception as e:
        db.session.rollback()
        logging.exception(f'Booking request error for user {current_user.id}: {e}')
        return error('An error occurred. Please try again.', 500)
    return (booking, status, auto_confirmed, same_day), None


@client_bp.route("/")
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

    # Upcoming (today onwards) non-cancelled bookings only. Past days are
    # fetched one at a time from day_bookings when the client taps a past
    # calendar date, so this page doesn't grow with the client's history.
    today = local_today()
    _index_dog_ids = [d.id for d in user_dogs]
    upcoming_bookings = Booking.query.options(
        joinedload(Booking.walker).joinedload(Walker.user),
        joinedload(Booking.service_type),
    ).filter(
        Booking.dog_id.in_(_index_dog_ids),
        Booking.date >= today,
        Booking.status.notin_(['cancelled', 'rejected']),
    ).all()
    upcoming_bookings.sort(key=_booking_sort_key)
    for b in upcoming_bookings:
        b.date_display = b.date.strftime("%a %d %b")
        b.is_drop_in = b.service_type and b.service_type.slug == ServiceType.DROP_IN

    # Rendered only for its CSRF token (form.hidden_tag()) — client-home.js
    # reads it and posts every booking to the JSON endpoints below.
    form = BookingForm()

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
    """AJAX single walk booking — returns JSON, no page reload.

    Accepts JSON body: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon", "dog_id"? }
    Returns: { "success": bool, "status": str, "message": str, "booking": {...} }
    """
    result, error = _create_booking_request(
        ServiceType.WALK, auto_confirm=True,
        missing_service=('No service type available. Please contact support.', 500),
    )
    if error:
        return error
    booking, status, auto_confirmed, same_day = result
    day = booking.date.strftime('%d %b')

    if status == 'waitlisted':
        message = (f"All slots are full — you've been added to the waitlist "
                   f"for {booking.slot} on {day}.")
    elif auto_confirmed:
        message = f"Booking confirmed for {booking.slot} on {day}!"
    elif same_day:
        message = f"Same-day request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
    else:
        message = "Booking request submitted — we'll confirm it shortly."

    return jsonify({
        'success': True,
        'status':  status,
        'message': message,
        'booking': {
            'id':               booking.id,
            'date_display':     booking.date.strftime('%a %-d %b'),
            'date_iso':         booking.date.isoformat(),
            'slot':             booking.slot,
            'status':           status,
            'dog_id':           booking.dog_id,
            'walker_name':      booking.walker.user.firstname if booking.walker_id else None,
            'has_pickup_notes': bool(booking.dog.pickup_instructions),
        },
    })


@client_bp.route("/book_both", methods=["POST"])
@login_required
def book_both():
    """AJAX endpoint: request both Morning and Afternoon for a single date.

    Accepts JSON: { "date": "YYYY-MM-DD" }
    Each slot is booked independently — one can be requested while the other
    is waitlisted if capacity is tight. Returns both results.
    """
    data = request.get_json(silent=True) or {}
    request_fields, invalid = _validate_booking_request(data, with_slot=False)
    if invalid:
        return invalid
    booking_date, _, dog = request_fields
    dog_id = dog.id

    default_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
    if not default_service:
        return jsonify({'success': False, 'message': 'No service type available.'}), 500

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
            Booking.status.in_(_ACTIVE_STATUSES),
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
    """AJAX endpoint: request a drop-in visit for a given date + slot. Drop-ins
    never auto-confirm — the owner assigns them.

    Accepts JSON: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon", "dog_id"? }
    Returns JSON response (success/failure + booking info).
    """
    result, error = _create_booking_request(
        ServiceType.DROP_IN, auto_confirm=False,
        missing_service=('Drop-in service is not currently available.', 503),
    )
    if error:
        return error
    booking, status, _, same_day = result

    if same_day:
        message = f"Same-day drop-in request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
    elif status == 'waitlisted':
        message = (f"All drop-in slots are full for {booking.slot} on "
                   f"{booking.date.strftime('%d %b')}. You've been added to the waitlist.")
    else:
        message = "Drop-in request submitted — we'll confirm shortly."

    return jsonify({
        'success':  True,
        'status':   status,
        'message':  message,
        'booking':  {
            'id':           booking.id,
            'dog_id':       booking.dog_id,
            'date_display': booking.date.strftime('%a %-d %b'),
            'date_iso':     booking.date.isoformat(),
            'slot':         booking.slot,
            'status':       status,
            'is_drop_in':   True,
        },
    })


def _pause_scope(start_raw, end_raw, slots_raw):
    """Validate a Pause Walks request and return (bookings, None) or
    (None, error_response). Shared by the preview and the action so both pick
    exactly the same bookings — every active booking of every service
    (walks and drop-ins) for the client's dogs in the range.

    slots_raw: 0 or 2 valid values = both slots; exactly 1 narrows to it.
    """
    try:
        start = date_type.fromisoformat(start_raw or '')
        end   = date_type.fromisoformat(end_raw or '')
    except (ValueError, TypeError):
        return None, (jsonify(success=False, error="Invalid dates"), 400)

    if start <= local_today():
        return None, (jsonify(success=False, error="Start date must be in the future"), 400)
    if end < start:
        return None, (jsonify(success=False, error="End date must be after start date"), 400)
    if (end - start).days > 365:
        return None, (jsonify(success=False, error="Range cannot exceed one year"), 400)

    slot_filter = [s for s in (slots_raw or []) if s in ('Morning', 'Afternoon')]

    dog_ids = get_accessible_dog_ids(current_user.id)
    q = Booking.query.options(
        joinedload(Booking.service_type), joinedload(Booking.dog),
    ).filter(
        Booking.dog_id.in_(dog_ids),
        Booking.date >= start,
        Booking.date <= end,
        Booking.status.notin_(['cancelled', 'rejected', 'completed']),
    )
    if len(slot_filter) == 1:
        q = q.filter(Booking.slot == slot_filter[0])
    bookings = q.all()
    bookings.sort(key=_booking_sort_key)
    return bookings, None


@client_bp.route("/pause-walks/preview")
@login_required
def pause_walks_preview():
    """Return bookings that would be cancelled in a date range (no writes).

    `charged` per row is the same bill_cancellation_for() decision the action
    persists, so the warning the client sees is exactly what gets billed.
    """
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403
    bookings, error = _pause_scope(request.args.get('start'), request.args.get('end'),
                                   request.args.getlist('slots'))
    if error:
        return error

    today = local_today()
    rows = [{
        'date':       b.date.strftime('%-d %b'),
        'slot':       b.slot,
        'dog':        b.dog.name if b.dog else '',
        'is_drop_in': bool(b.service_type and b.service_type.slug == ServiceType.DROP_IN),
        'charged':    bill_cancellation_for(b, cancelled_by='client', today=today),
    } for b in bookings]
    return jsonify(
        success=True,
        count=len(rows),
        charged_count=sum(r['charged'] for r in rows),
        bookings=rows,
    )


@client_bp.route("/pause-walks", methods=["POST"])
@login_required
def pause_walks():
    """Cancel all active bookings for the client's dogs within a date range."""
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403
    data = request.get_json(silent=True) or {}
    bookings, error = _pause_scope(data.get('start'), data.get('end'), data.get('slots'))
    if error:
        return error

    if not bookings:
        return jsonify(success=True, cancelled_count=0)

    today      = local_today()
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
        # committed status.
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


@client_bp.route("/day_bookings/<date_iso>")
@login_required
def day_bookings(date_iso):
    """Return this client's bookings on one date, for the home page's
    calendar filter when the client taps a past date (the page itself only
    carries upcoming bookings). Same dog access and statuses as
    calendar_data."""
    try:
        day = date_type.fromisoformat(date_iso)
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    bookings = Booking.query.options(
        joinedload(Booking.walker).joinedload(Walker.user),
        joinedload(Booking.service_type),
    ).filter(
        Booking.dog_id.in_(get_accessible_dog_ids(current_user.id)),
        Booking.date == day,
        Booking.status.notin_(['cancelled', 'rejected']),
    ).all()
    bookings.sort(key=_booking_sort_key)

    return jsonify(success=True, date=day.isoformat(), bookings=[{
        'id':           b.id,
        'dog_id':       b.dog_id,
        'date_display': b.date.strftime("%a %d %b"),
        'slot':         b.slot,
        'status':       b.status,
        'walker_name':  (b.walker.user.firstname
                         if b.status == 'confirmed' and b.walker and b.walker.user else None),
        'is_drop_in':   bool(b.service_type and b.service_type.slug == ServiceType.DROP_IN),
        'note':         b.client_notes or '',
    } for b in bookings])


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

        today = local_today()
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
                        Booking.status.in_(_ACTIVE_STATUSES),
                    ).first()
                    if existing:
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

    # A past walk's note is view-only (client home shows it read-only). Only
    # client home calls this route; admins edit notes via the board's
    # update_booking_note_admin, which this doesn't restrict.
    if booking.date < local_today():
        return jsonify(success=False, message="This walk is in the past, so its note can't be changed"), 400

    data = request.get_json(silent=True) or {}
    note = (data.get('note') or '').strip()
    if len(note) > Booking.NOTE_MAX_LENGTH:
        return jsonify(success=False,
                       message=f"Note must be {Booking.NOTE_MAX_LENGTH} characters or fewer"), 400

    booking.client_notes = note or None
    db.session.commit()
    return jsonify(success=True, note=booking.client_notes)
