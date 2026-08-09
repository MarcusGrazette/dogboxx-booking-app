"""
Stable per-walker visual identity — a color and initials derived from the
walker record, shared by every surface that shows a walker avatar (assignment
board, admin weekly overview) so the same walker never shows two different
colors on two different pages.
"""

WALKER_COLORS = [
    '#8b5cf6',  # violet
    '#ec4899',  # pink
    '#f97316',  # orange
    '#14b8a6',  # teal
    '#3b82f6',  # blue
    '#a855f7',  # purple
    '#10b981',  # emerald
    '#f59e0b',  # amber
    '#6366f1',  # indigo
    '#84cc16',  # lime
]


def walker_color(walker_id):
    return WALKER_COLORS[walker_id % len(WALKER_COLORS)]


def walker_initials(walker):
    if not walker.user:
        return '?'
    first = (walker.user.firstname or '')[:1].upper()
    last = (walker.user.lastname or '')[:1].upper()
    return (first + last) if last else first
