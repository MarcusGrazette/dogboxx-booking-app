from flask import request, render_template, url_for
from flask_login import login_required
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import (
    User, Booking, BookingStatusChange, Walker, WalkerUnavailability,
    WalkerAdHocAvailability, ServiceType, Closure, Broadcast,
)
from app import db


@admin_bp.route("/activity")
@login_required
@admin_required
def activity_feed():
    """Admin activity feed — rebuilt from the action log (§9.6, Session 4).

    Sources: BookingStatusChange (every booking transition), WalkerUnavailability,
    WalkerAdHocAvailability, Closure, Broadcast. Actor attribution read from each
    row's actor FK — never inferred from booking ownership (P2).
    """
    from datetime import date as date_type
    from sqlalchemy import func

    # ── Month selection ────────────────────────────────────────────────────────
    month_str = request.args.get('month', '')
    try:
        if len(month_str) == 7 and month_str[4] == '-':
            month_start = date_type(int(month_str[:4]), int(month_str[5:7]), 1)
        else:
            raise ValueError
    except (ValueError, IndexError):
        today = date_type.today()
        month_start = date_type(today.year, today.month, 1)

    if month_start.month == 12:
        month_end = date_type(month_start.year + 1, 1, 1)
    else:
        month_end = date_type(month_start.year, month_start.month + 1, 1)

    dt_start = datetime(month_start.year, month_start.month, 1)
    dt_end   = datetime(month_end.year,   month_end.month,   1)

    # Badge / icon metadata keyed by badge slug
    BADGE_META = {
        'confirmed':   ('Booked',      'bi-check-circle-fill',   '#198754', 'rgba(25,135,84,0.11)'),
        'requested':   ('Requested',   'bi-calendar-plus-fill',  '#b02280', 'rgba(224,47,172,0.11)'),
        'waitlisted':  ('Waitlisted',  'bi-hourglass-split',     '#8a6500', 'rgba(255,193,7,0.18)'),
        'cancelled':   ('Cancelled',   'bi-x-circle-fill',       '#bb2d3b', 'rgba(220,53,69,0.11)'),
        'rejected':    ('Declined',    'bi-x-circle-fill',       '#bb2d3b', 'rgba(220,53,69,0.11)'),
        'unavailable': ('Unavailable', 'bi-calendar-x-fill',     '#c45c00', 'rgba(253,126,20,0.11)'),
        'available':   ('Available',   'bi-calendar-check-fill', '#13877c', 'rgba(20,184,166,0.11)'),
        'closure':     ('Closed',      'bi-shop-window',         '#bb2d3b', 'rgba(220,53,69,0.11)'),
        'broadcast':   ('Broadcast',   'bi-megaphone-fill',      '#0d6efd', 'rgba(13,110,253,0.11)'),
    }

    def _actor_type(user):
        if user.is_admin:
            return 'admin'
        if user.role == 'walker':
            return 'walker'
        return 'client'

    def _make_event(ts, actor_type, actor_name, actor_id, description, badge, activity_type, link,
                    batch_id=None, booking_date=None, dog_name_raw=None, svc_label_raw=None,
                    booking_id=None):
        parts = actor_name.split()
        initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else '')).upper() if parts else '?'
        label, icon, icon_color, badge_bg = BADGE_META.get(badge, ('', 'bi-circle', '#666', 'rgba(0,0,0,0.06)'))
        return dict(ts=ts, actor_type=actor_type, actor_name=actor_name, actor_id=actor_id,
                    description=description, badge=badge, activity_type=activity_type, link=link,
                    initials=initials, badge_label=label, icon=icon, icon_color=icon_color, badge_bg=badge_bg,
                    batch_id=batch_id, booking_date=booking_date,
                    dog_name_raw=dog_name_raw, svc_label_raw=svc_label_raw,
                    booking_id=booking_id, is_cluster=False)

    def _cluster_events(event_list):
        """Group BSC events sharing a batch_id into collapsible clusters (D4).
        Non-batch events and single-row batches pass through unchanged."""
        from collections import defaultdict, Counter
        # Sort first so clusters appear at the position of their latest child.
        event_list.sort(key=lambda e: e['ts'] if e['ts'] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        # Collect all children per batch_id and track first-seen position.
        batch_children = defaultdict(list)
        seen = set()
        ordered_batches = []
        result = []

        for e in event_list:
            bid = e.get('batch_id')
            if bid:
                batch_children[bid].append(e)
                if bid not in seen:
                    seen.add(bid)
                    ordered_batches.append(bid)
            else:
                result.append(e)

        batch_events = []
        for bid in ordered_batches:
            children = batch_children[bid]
            if len(children) == 1:
                batch_events.append(children[0])
            else:
                batch_events.append(_make_cluster(bid, children))

        # Merge non-batch and batch events, re-sort
        combined = result + batch_events
        combined.sort(key=lambda e: e['ts'] if e['ts'] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return combined

    def _make_cluster(bid, children):
        """Build a single cluster-row dict from N children sharing a batch_id."""
        from collections import Counter, defaultdict
        ts          = max((c['ts'] for c in children if c['ts']), default=None)
        actor_type  = children[0]['actor_type']
        actor_name  = children[0]['actor_name']
        actor_id    = children[0]['actor_id']
        initials    = children[0]['initials']
        activity_type = children[0]['activity_type']
        link        = children[0]['link']

        # Deduplicate by booking_id: for a booking that transitions
        # requested→confirmed, two BSC rows share the same batch_id.
        # Keep only the latest-ts row per booking so the count and badge
        # reflect the final/terminal state rather than counting each
        # transition separately.
        by_booking = defaultdict(list)
        for c in children:
            bid_key = c.get('booking_id')
            if bid_key is not None:
                by_booking[bid_key].append(c)
        if by_booking:
            deduped = [max(rows, key=lambda r: r['ts'] or datetime.min.replace(tzinfo=timezone.utc))
                       for rows in by_booking.values()]
            deduped.extend(c for c in children if c.get('booking_id') is None)
        else:
            deduped = children

        # Dominant badge = most common final status across unique bookings
        badge = Counter(c['badge'] for c in deduped).most_common(1)[0][0]
        label, icon, icon_color, badge_bg = BADGE_META.get(badge, ('', 'bi-circle', '#666', 'rgba(0,0,0,0.06)'))

        # Build summary description from structured fields
        dog_names = sorted({c['dog_name_raw'] for c in deduped if c.get('dog_name_raw')})
        dates     = [c['booking_date'] for c in deduped if c.get('booking_date')]
        svcs      = {c.get('svc_label_raw', 'walk') for c in deduped}
        svc       = svcs.pop() if len(svcs) == 1 else 'walk'
        n         = len(deduped)
        plural    = 's' if n != 1 else ''

        if dates:
            lo, hi = min(dates), max(dates)
            if lo == hi:
                span = lo.strftime('%a %-d %b')
            elif (lo.month, lo.year) == (hi.month, hi.year):
                span = f"{lo.strftime('%a %-d')} – {hi.strftime('%a %-d %b')}"
            else:
                span = f"{lo.strftime('%a %-d %b')} – {hi.strftime('%a %-d %b')}"
        else:
            span = None

        verb_map = {
            'confirmed': 'confirmed', 'cancelled': 'cancelled',
            'rejected': 'declined',   'requested': 'requested',
            'waitlisted': 'waitlisted',
        }
        verb     = verb_map.get(badge, badge)
        dog_pfx  = f"{dog_names[0]}'s " if len(dog_names) == 1 else ''
        desc     = f"{dog_pfx}{n} {svc}{plural} {verb}"
        if span:
            desc += f" ({span})"
        if len(dog_names) > 1:
            desc += f" · {', '.join(dog_names)}"

        return dict(
            ts=ts, actor_type=actor_type, actor_name=actor_name, actor_id=actor_id,
            description=desc, badge=badge, activity_type=activity_type, link=link,
            initials=initials, badge_label=label, icon=icon, icon_color=icon_color, badge_bg=badge_bg,
            batch_id=bid, booking_date=None, dog_name_raw=None, svc_label_raw=None,
            is_cluster=True, cluster_count=n, children=children,
        )

    events = []

    # ── BookingStatusChange — one event per transition (P2: actor from log) ───
    bsc_rows = (
        BookingStatusChange.query
        .options(
            joinedload(BookingStatusChange.booking)
                .joinedload(Booking.dog),
            joinedload(BookingStatusChange.booking)
                .joinedload(Booking.service_type),
            joinedload(BookingStatusChange.booking)
                .joinedload(Booking.user),
            joinedload(BookingStatusChange.changed_by),
        )
        .filter(BookingStatusChange.created_at >= dt_start,
                BookingStatusChange.created_at < dt_end)
        .all()
    )

    # Suppress the creation-as-requested row for any booking that was
    # immediately auto-confirmed in the same batch. Same batch_id means
    # same request — a booking requested then confirmed across two separate
    # admin actions has a different batch_id and keeps both rows visible.
    _immediate_confirms = {
        (bsc.booking_id, bsc.batch_id)
        for bsc in bsc_rows
        if bsc.to_status == 'confirmed' and bsc.batch_id
    }

    for bsc in bsc_rows:
        if (bsc.from_status is None and bsc.to_status == 'requested'
                and bsc.batch_id
                and (bsc.booking_id, bsc.batch_id) in _immediate_confirms):
            continue
        b = bsc.booking
        actor = bsc.changed_by
        if not b or not actor or not b.dog:
            continue
        svc_label = 'drop-in' if (b.service_type and b.service_type.slug == ServiceType.DROP_IN) else 'walk'
        walk_date = b.date.strftime('%a %-d %b') if b.date else '?'
        dog = b.dog.name
        slot = b.slot.lower() if b.slot else ''
        atype = _actor_type(actor)
        client_link = url_for('admin.client_detail', client_id=b.user_id)

        ts = bsc.to_status
        if ts == 'confirmed':
            # A slot-override re-confirm has old_slot/new_slot set (F6) — show
            # it as a move rather than an indistinguishable re-confirm.
            if bsc.old_slot is not None:
                desc = f"Moved {dog}'s {svc_label} on {walk_date} to {slot}"
            else:
                desc = f"Confirmed {dog}'s {slot} {svc_label} on {walk_date}"
            if b.user and atype == 'admin':
                desc += f" for {b.user.full_name}"
            badge, activity_type = 'confirmed', 'booking'
        elif ts in ('cancelled', 'rejected'):
            verb = 'Declined' if ts == 'rejected' else 'Cancelled'
            desc = f"{verb} {dog}'s {slot} {svc_label} on {walk_date}"
            if b.user and atype == 'admin':
                desc += f" ({b.user.full_name})"
            badge, activity_type = ts, 'cancellation'
        elif ts == 'waitlisted':
            desc = f"Waitlisted {dog}'s {slot} {svc_label} on {walk_date}"
            if b.user and atype == 'admin':
                desc += f" for {b.user.full_name}"
            badge, activity_type = 'waitlisted', 'booking'
        else:  # requested
            if bsc.from_status is None:
                desc = f"Requested {dog}'s {slot} {svc_label} on {walk_date}"
                if b.user and atype == 'admin':
                    desc += f" for {b.user.full_name}"
            else:
                desc = f"Reset {dog}'s {slot} {svc_label} on {walk_date} to requested"
            badge, activity_type = 'requested', 'booking'

        events.append(_make_event(
            ts=bsc.created_at, actor_type=atype,
            actor_name=actor.full_name, actor_id=actor.id,
            description=desc, badge=badge, activity_type=activity_type,
            link=client_link,
            batch_id=bsc.batch_id, booking_date=b.date,
            dog_name_raw=b.dog.name, svc_label_raw=svc_label,
            booking_id=b.id,
        ))

    # ── Walker unavailabilities ────────────────────────────────────────────────
    for u in (WalkerUnavailability.query
              .options(joinedload(WalkerUnavailability.walker).joinedload(Walker.user),
                       joinedload(WalkerUnavailability.created_by))
              .filter(WalkerUnavailability.created_at >= dt_start,
                      WalkerUnavailability.created_at < dt_end)
              .all()):
        if not u.walker or not u.walker.user or not u.created_at:
            continue
        # Actor: admin who added it, or the walker themselves
        if u.created_by_id and u.created_by:
            actor = u.created_by
        else:
            actor = u.walker.user
        avail_date = u.date.strftime('%a %-d %b') if u.date else '?'
        events.append(_make_event(
            ts=u.created_at, actor_type=_actor_type(actor),
            actor_name=actor.full_name, actor_id=actor.id,
            description=f"Marked {u.walker.user.full_name} unavailable — {u.slot} on {avail_date}",
            badge='unavailable', activity_type='availability',
            link=url_for('admin.walkers'),
        ))

    # ── Walker adhoc availabilities ────────────────────────────────────────────
    for a in (WalkerAdHocAvailability.query
              .options(joinedload(WalkerAdHocAvailability.walker).joinedload(Walker.user),
                       joinedload(WalkerAdHocAvailability.created_by))
              .filter(WalkerAdHocAvailability.created_at >= dt_start,
                      WalkerAdHocAvailability.created_at < dt_end)
              .all()):
        if not a.walker or not a.walker.user or not a.created_at:
            continue
        if a.created_by_id and a.created_by:
            actor = a.created_by
        else:
            actor = a.walker.user
        avail_date = a.date.strftime('%a %-d %b') if a.date else '?'
        events.append(_make_event(
            ts=a.created_at, actor_type=_actor_type(actor),
            actor_name=actor.full_name, actor_id=actor.id,
            description=f"Added {a.slot.lower()} availability for {a.walker.user.full_name} on {avail_date}",
            badge='available', activity_type='availability',
            link=url_for('admin.walkers'),
        ))

    # ── Closures ──────────────────────────────────────────────────────────────
    for c in (Closure.query
              .options(joinedload(Closure.created_by))
              .filter(Closure.created_at >= dt_start,
                      Closure.created_at < dt_end)
              .all()):
        if not c.created_at:
            continue
        actor = c.created_by
        if not actor:
            continue
        close_date = c.date.strftime('%a %-d %b') if c.date else '?'
        desc = f"DogBoxx closed on {close_date}"
        if c.reason:
            desc += f" — {c.reason}"
        events.append(_make_event(
            ts=c.created_at, actor_type=_actor_type(actor),
            actor_name=actor.full_name, actor_id=actor.id,
            description=desc, badge='closure', activity_type='closure',
            link=url_for('admin.closures'),
        ))

    # ── Broadcasts ────────────────────────────────────────────────────────────
    for br in (Broadcast.query
               .options(joinedload(Broadcast.sender))
               .filter(Broadcast.sent_at >= dt_start,
                       Broadcast.sent_at < dt_end)
               .all()):
        if not br.sender:
            continue
        scope_date = br.scope_date.strftime('%a %-d %b') if br.scope_date else '?'
        scope_label = {'all': 'all-day', 'morning': 'morning', 'afternoon': 'afternoon'}.get(
            br.scope_slot, br.scope_slot)
        desc = (f"Broadcast to {br.recipient_count} client{'s' if br.recipient_count != 1 else ''} "
                f'on {scope_date} ({scope_label}) — "{br.subject}"')
        events.append(_make_event(
            ts=br.sent_at, actor_type=_actor_type(br.sender),
            actor_name=br.sender.full_name, actor_id=br.sender_id,
            description=desc, badge='broadcast', activity_type='broadcast',
            link=url_for('admin.broadcasts'),
        ))

    events = _cluster_events(events)

    # Month dropdown — earliest event across all log sources
    candidates = [
        db.session.query(func.min(BookingStatusChange.created_at)).scalar(),
        db.session.query(func.min(WalkerUnavailability.created_at)).scalar(),
        db.session.query(func.min(WalkerAdHocAvailability.created_at)).scalar(),
        db.session.query(func.min(Closure.created_at)).scalar(),
        db.session.query(func.min(Broadcast.sent_at)).scalar(),
    ]
    earliest = None
    for ts in candidates:
        if ts and (earliest is None or ts < earliest):
            earliest = ts

    today = date_type.today()
    oldest = date_type(earliest.year, earliest.month, 1) if earliest else date_type(today.year, today.month, 1)

    month_options = []
    y, m = today.year, today.month
    while date_type(y, m, 1) >= oldest:
        month_options.append(date_type(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1

    return render_template('admin_activity.html',
                           events=events,
                           month_start=month_start,
                           month_options=month_options)
