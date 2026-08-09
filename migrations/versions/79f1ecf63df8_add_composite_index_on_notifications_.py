"""add composite index on notifications recipient_id created_at

Revision ID: 79f1ecf63df8
Revises: ad12a36f7ce0
Create Date: 2026-08-09 08:53:37.694477

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '79f1ecf63df8'
down_revision = 'ad12a36f7ce0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'ix_notif_recipient_created', 'notifications',
        ['recipient_id', 'created_at'],
    )


def downgrade():
    op.drop_index('ix_notif_recipient_created', table_name='notifications')
