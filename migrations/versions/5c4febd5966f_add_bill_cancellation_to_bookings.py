"""add bill_cancellation to bookings

Revision ID: 5c4febd5966f
Revises: e05ff71bb82d
Create Date: 2026-06-13 12:06:37.274480

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5c4febd5966f'
down_revision = 'e05ff71bb82d'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable: NULL = "use default late-cancel policy" for every existing row,
    # so already-issued invoices are unaffected. True/False set only when an
    # admin makes an explicit bill/waive choice at cancel time.
    op.add_column('bookings', sa.Column('bill_cancellation', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('bookings', 'bill_cancellation')
