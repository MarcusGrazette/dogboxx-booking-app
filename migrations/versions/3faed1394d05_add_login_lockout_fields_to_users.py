"""add login lockout fields to users

Revision ID: 3faed1394d05
Revises: 79f1ecf63df8
Create Date: 2026-08-20 13:56:18.110915

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3faed1394d05'
down_revision = '79f1ecf63df8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('locked_until', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_login_attempts')
