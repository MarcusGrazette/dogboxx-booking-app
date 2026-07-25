"""
Admin weekly overview — a manual check-in tool for the owner: a top-of-page
card showing which walkers are on AM/PM each day, then a per-walker list
with a day-by-day breakdown and a Copy button for pasting a plain-text
summary into email/WhatsApp.

Data source is identical to the walker-facing weekly overview (same
app.utils.weekly_schedule helpers) — "scheduled to work" means actually has
a dog booked, not just generic availability.
"""
from datetime import datetime, timezone, timedelta

from flask import request, render_template, flash, redirect, url_for
from flask_login import login_required

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.utils.weekly_schedule import (
    get_week_start,
    fetch_week_bookings,
    build_week_by_day,
    build_week_by_walker,
    format_walker_week_text,
    format_roster_week_text,
    day_slot_parts,
    day_walker_names,
    WEEKDAYS,
    WEEKDAY_LABELS,
)


@admin_bp.route("/weekly-overview")
@admin_bp.route("/weekly-overview/<date_str>")
@login_required
@admin_required
def weekly_overview(date_str=None):
    """List every walker with a booking this week, each with a day-by-day
    breakdown and a ready-to-copy plain-text summary of their week."""
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

    bookings = fetch_week_bookings(week_start)

    # Top-of-page "who's working, by day" summary — same underlying grouping
    # the walker weekly view uses, just rendered as AM/PM walker names
    # instead of per-slot walker cards.
    week_by_day = build_week_by_day(bookings, week_start)
    roster_by_day = [
        {
            'label': WEEKDAY_LABELS[i],
            'date': week_start + timedelta(days=i),
            'parts': day_walker_names(week_by_day[week_start + timedelta(days=i)]),
        }
        for i in range(WEEKDAYS)
    ]
    roster_copy_text = format_roster_week_text(week_by_day, week_start)

    walker_weeks = []
    for entry in build_week_by_walker(bookings):
        walker = entry['walker']
        days = entry['days']

        day_rows = []
        total_dogs = 0
        for i in range(WEEKDAYS):
            d = week_start + timedelta(days=i)
            day_bookings = days.get(d, [])
            total_dogs += len(day_bookings)
            day_rows.append({
                'label': WEEKDAY_LABELS[i],
                'parts': day_slot_parts(day_bookings),
            })

        walker_weeks.append({
            'walker': walker,
            'day_rows': day_rows,
            'total_dogs': total_dogs,
            'copy_text': format_walker_week_text(walker, days, week_start),
        })

    return render_template(
        "admin_weekly_overview.html",
        week_start=week_start,
        week_end=week_end,
        roster_by_day=roster_by_day,
        roster_copy_text=roster_copy_text,
        walker_weeks=walker_weeks,
        prev_week=(week_start - timedelta(days=7)).strftime('%Y-%m-%d'),
        next_week=(week_start + timedelta(days=7)).strftime('%Y-%m-%d'),
    )
