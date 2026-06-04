"""add old_slot new_slot to booking_status_changes

Revision ID: e05ff71bb82d
Revises: f67a2a1712ad
Create Date: 2026-06-03 13:57:33.749624

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e05ff71bb82d'
down_revision = 'f67a2a1712ad'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('booking_status_changes',
                  sa.Column('old_slot', sa.String(32), nullable=True))
    op.add_column('booking_status_changes',
                  sa.Column('new_slot', sa.String(32), nullable=True))


def downgrade():
    op.drop_column('booking_status_changes', 'new_slot')
    op.drop_column('booking_status_changes', 'old_slot')
