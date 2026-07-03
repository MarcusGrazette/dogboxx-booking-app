"""add indexes on booking_status_changes.booking_id and push_subscriptions.user_id

Revision ID: 8763c31f1b76
Revises: 5c4febd5966f
Create Date: 2026-07-03 17:54:56.970766

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8763c31f1b76'
down_revision = '5c4febd5966f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        op.f('ix_booking_status_changes_booking_id'),
        'booking_status_changes', ['booking_id'], unique=False,
    )
    op.create_index(
        op.f('ix_push_subscriptions_user_id'),
        'push_subscriptions', ['user_id'], unique=False,
    )


def downgrade():
    op.drop_index(
        op.f('ix_push_subscriptions_user_id'),
        table_name='push_subscriptions',
    )
    op.drop_index(
        op.f('ix_booking_status_changes_booking_id'),
        table_name='booking_status_changes',
    )
