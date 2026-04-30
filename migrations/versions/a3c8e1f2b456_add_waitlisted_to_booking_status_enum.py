"""add waitlisted to booking_status enum

Revision ID: a3c8e1f2b456
Revises: f1bfa22e3f85
Create Date: 2026-04-07 19:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c8e1f2b456'
down_revision = 'f1bfa22e3f85'
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL requires ALTER TYPE to add a new value to an existing enum.
    # SQLite has no named enum types — the value is stored as a plain string.
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'waitlisted'")


def downgrade():
    # PostgreSQL does not support removing values from an enum without
    # recreating it. A downgrade here would require recreating the type and
    # all columns that reference it, which risks data loss. Leave as a no-op —
    # the extra enum value is harmless if this migration is rolled back.
    pass
