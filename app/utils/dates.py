from datetime import date


def parse_month_param(month_str, today):
    """Parse 'YYYY-MM' into (month_start, month_end). Falls back to the current
    month for anything unparseable — including years that datetime.date rejects
    (year 0, or a December that would roll month_end past year 9999)."""
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
        month_start = date(year, month, 1)
        month_end = date(year + (month // 12), (month % 12) + 1, 1)
    except (ValueError, IndexError, TypeError):
        month_start = date(today.year, today.month, 1)
        month_end = date(today.year + (today.month // 12), (today.month % 12) + 1, 1)
    return month_start, month_end
