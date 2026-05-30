"""Add batch_id to booking_status_changes

Revision ID: 6913631b986e
Revises: b40f4de664d4
Create Date: 2026-05-30 07:24:27.386244

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6913631b986e'
down_revision = 'b40f4de664d4'
branch_labels = None
depends_on = None


def upgrade():
    # Correlates BSC rows produced by one bulk action so the activity feed can
    # collapse them into a single expandable cluster (NOTIFICATIONS.md §9.2, D4).
    op.add_column(
        'booking_status_changes',
        sa.Column('batch_id', sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f('ix_booking_status_changes_batch_id'),
        'booking_status_changes', ['batch_id'], unique=False,
    )


def downgrade():
    op.drop_index(
        op.f('ix_booking_status_changes_batch_id'),
        table_name='booking_status_changes',
    )
    op.drop_column('booking_status_changes', 'batch_id')
