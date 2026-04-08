"""add weekly_discount to pricing_configs

Revision ID: c9e3a1f7d205
Revises: b7d2f4e9c123
Create Date: 2026-04-08 13:00:00.000000

Weekly discount: a per-walk fixed-amount discount applied to all confirmed
group walks in any week where the client has 5 or more confirmed walks.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9e3a1f7d205'
down_revision = 'b7d2f4e9c123'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pricing_configs') as batch_op:
        batch_op.add_column(sa.Column(
            'weekly_discount',
            sa.Numeric(precision=8, scale=2),
            nullable=False,
            server_default='0.00',
        ))


def downgrade():
    with op.batch_alter_table('pricing_configs') as batch_op:
        batch_op.drop_column('weekly_discount')
