"""Add Booking.created_by_id for admin attribution

Revision ID: fa6d5571aa08
Revises: 6c2e474e3b02
Create Date: 2026-05-28 20:22:45.092572

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fa6d5571aa08'
down_revision = '6c2e474e3b02'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_bookings_created_by_id_users',
            'users', ['created_by_id'], ['id'],
        )


def downgrade():
    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.drop_constraint('fk_bookings_created_by_id_users', type_='foreignkey')
        batch_op.drop_column('created_by_id')
