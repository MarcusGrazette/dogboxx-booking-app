"""Shared (start_date, end_date) range parsing for admin date-range actions
(Closures, Freezes) — both accept either explicit start_date/end_date keys or
a legacy single `date` key, and both group the per-date rows they create
under one range_id. Extracted here so the two call sites can't drift."""

from datetime import datetime, timedelta


def parse_range(source, *, max_days):
    """Resolve a (start, end) date pair from either explicit start_date/end_date
    keys or a legacy single `date` key (start == end). Raises ValueError with a
    user-facing message on any invalid input."""
    single = source.get('date', '')
    start_str = source.get('start_date') or single
    end_str = source.get('end_date') or single

    try:
        start = datetime.strptime(start_str, '%Y-%m-%d').date()
        end = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError("Invalid date")

    if end < start:
        raise ValueError("End date is before start date")
    if (end - start).days + 1 > max_days:
        raise ValueError(f"Range too long — max {max_days} days")

    return start, end


def dates_in_range(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def range_label(dates):
    if len(dates) == 1:
        return dates[0].strftime('%-d %b %Y')
    return f"{dates[0].strftime('%-d %b')} – {dates[-1].strftime('%-d %b %Y')}"
