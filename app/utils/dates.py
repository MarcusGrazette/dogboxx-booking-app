from datetime import date, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo('Europe/London')


def to_local_time(dt):
    """Convert a UTC datetime to Europe/London (correctly handling BST).

    `db.DateTime` columns are TIMESTAMP WITHOUT TIME ZONE — even though every
    row is written via `datetime.now(timezone.utc)`, SQLAlchemy hands back a
    naive datetime on read. Treat a naive value as UTC before converting."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def _month_bounds(year, month):
    return date(year, month, 1), date(year + (month // 12), (month % 12) + 1, 1)


def parse_month_param(month_str, today, *, cap_at_today=False):
    """Parse 'YYYY-MM' into (month_start, month_end). Falls back to the current
    month for anything unparseable — including years that datetime.date rejects
    (year 0, or a December that would roll month_end past year 9999).

    cap_at_today=True additionally clamps a future month back to the current
    one (no peeking ahead at a month that hasn't happened yet) — used by the
    client monthly summary, not the admin invoicing views."""
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
        month_start, month_end = _month_bounds(year, month)
    except (ValueError, IndexError, TypeError):
        month_start, month_end = _month_bounds(today.year, today.month)

    if cap_at_today and (month_start.year, month_start.month) > (today.year, today.month):
        month_start, month_end = _month_bounds(today.year, today.month)

    return month_start, month_end
