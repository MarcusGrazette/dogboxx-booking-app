"""
Admin "Week view" — the week as a grid: one column per walker with a booking
that week, each weekday a band of rows (AM drop-ins, AM walks, PM walks, PM
drop-ins), dogs as cards showing their walk note with a pencil to edit it.
Each row counts its unassigned (requested/waitlisted) bookings, linking to
the dashboard on that day. Phones get the same data as a stacked list.

Copy buttons (per walker, and the whole team) carry plain-text summaries for
email/WhatsApp, built from confirmed bookings only and never including notes.
Shares app.utils.weekly_schedule with the walker-facing weekly view.
"""
from datetime import datetime, timezone, timedelta

from flask import request, render_template, flash, redirect, url_for
from flask_login import login_required

from app.blueprints.admin import admin_bp
from app.blueprints.admin.views.board import _batch_owners_display
from app.models import Booking
from app.utils.decorators import admin_required
from app.utils.weekly_schedule import (
    get_week_start,
    fetch_week_active_bookings,
    build_week_by_day,
    build_week_by_walker,
    build_week_grid,
    both_walks_keys,
    format_walker_week_text,
    format_roster_week_text,
    WEEKDAYS,
)
from app.utils.walker_visuals import walker_color, walker_initials


@admin_bp.route("/weekly-overview")
@admin_bp.route("/weekly-overview/<date_str>")
@login_required
@admin_required
def weekly_overview(date_str=None):
    """The admin Week view grid for the Mon-Fri week containing the given
    date (default: this week)."""
    today = datetime.now(timezone.utc).date()

    raw_date = date_str or request.args.get('date')
    if raw_date:
        try:
            anchor_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for('admin.weekly_overview'))
    else:
        anchor_date = today

    week_start = get_week_start(anchor_date)
    week_end = week_start + timedelta(days=WEEKDAYS - 1)

    bookings = fetch_week_active_bookings(week_start)
    # Copy text and walker columns come from confirmed, walker-assigned
    # bookings only — the same set the walker-facing weekly view uses.
    confirmed = [b for b in bookings
                 if b.status in Booking.WALKER_STATUSES and b.walker_id]

    team_copy_text = format_roster_week_text(build_week_by_day(confirmed, week_start), week_start)

    walkers = []
    for entry in build_week_by_walker(confirmed):
        walker = entry['walker']
        walkers.append({
            'id': walker.id,
            'name': walker.user.firstname if walker.user else 'Walker',
            'color': walker_color(walker.id),
            'initials': walker_initials(walker),
            'total_dogs': sum(len(day) for day in entry['days'].values()),
            'copy_text': format_walker_week_text(walker, entry['days'], week_start),
        })

    return render_template(
        "admin_week_view.html",
        week_start=week_start,
        week_end=week_end,
        at_current_week=(week_start == get_week_start(today)),
        walkers=walkers,
        days=build_week_grid(bookings, week_start, [w['id'] for w in walkers]),
        both_walks=both_walks_keys(bookings),
        owners=_batch_owners_display({b.dog_id for b in bookings if b.dog_id}),
        team_copy_text=team_copy_text,
        prev_week=(week_start - timedelta(days=7)).strftime('%Y-%m-%d'),
        next_week=(week_start + timedelta(days=7)).strftime('%Y-%m-%d'),
    )
