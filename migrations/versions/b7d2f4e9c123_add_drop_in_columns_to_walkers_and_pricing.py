"""add drop-in columns to walkers and pricing_configs

Revision ID: b7d2f4e9c123
Revises: a3c8e1f2b456
Create Date: 2026-04-07 19:45:00.000000

These columns were previously added via a standalone add_drop_in.py script
rather than a proper Alembic migration, so they were missing on fresh databases.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d2f4e9c123'
down_revision = 'a3c8e1f2b456'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('walkers') as batch_op:
        batch_op.add_column(sa.Column('does_drop_ins', sa.Boolean(), nullable=False, server_default='false'))

    with op.batch_alter_table('pricing_configs') as batch_op:
        batch_op.add_column(sa.Column('price_per_drop_in', sa.Numeric(precision=8, scale=2), nullable=False, server_default='5.00'))


def downgrade():
    with op.batch_alter_table('pricing_configs') as batch_op:
        batch_op.drop_column('price_per_drop_in')

    with op.batch_alter_table('walkers') as batch_op:
        batch_op.drop_column('does_drop_ins')
