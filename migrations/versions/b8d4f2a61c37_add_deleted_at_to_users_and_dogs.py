"""Add deleted_at to users and dogs (client/dog deletion, FEATURES #100)

Revision ID: b8d4f2a61c37
Revises: a7c3e91d2b40
Create Date: 2026-10-26 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8d4f2a61c37'
down_revision = 'a7c3e91d2b40'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, no backfill: NULL means "not deleted".
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('dogs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('dogs', schema=None) as batch_op:
        batch_op.drop_column('deleted_at')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('deleted_at')
