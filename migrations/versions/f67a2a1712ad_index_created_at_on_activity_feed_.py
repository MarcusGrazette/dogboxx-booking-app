"""index created_at on activity-feed sources

Revision ID: f67a2a1712ad
Revises: 8f826da874ff
Create Date: 2026-06-02 19:52:35.467572

The activity feed (NOTIFICATIONS.md §9.6) filters each log source by a
created_at month range. Index those columns so the range scan uses an index
instead of a sequential scan (review finding F4). Broadcast.sent_at was
already indexed; this covers the remaining four sources. Hand-written (no
autogenerate) per the CLAUDE.md SQLite enum modify_type trap — these are
pure additive index ops.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f67a2a1712ad'
down_revision = '8f826da874ff'
branch_labels = None
depends_on = None


# (table, index_name) — index_name matches SQLAlchemy's default ix_<table>_<col>
# so the model's index=True and the DB agree (no flask db check drift).
_INDEXES = [
    ('booking_status_changes',    'ix_booking_status_changes_created_at'),
    ('walker_unavailabilities',   'ix_walker_unavailabilities_created_at'),
    ('walker_adhoc_availability', 'ix_walker_adhoc_availability_created_at'),
    ('closures',                  'ix_closures_created_at'),
]


def upgrade():
    for table, index_name in _INDEXES:
        op.create_index(index_name, table, ['created_at'])


def downgrade():
    for table, index_name in _INDEXES:
        op.drop_index(index_name, table_name=table)
