"""add is_super_admin to users

Revision ID: 03cc1232ef8f
Revises: c9e3a1f7d205
Create Date: 2026-04-10 18:23:42.785909

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '03cc1232ef8f'
down_revision = 'c9e3a1f7d205'
branch_labels = None
depends_on = None

OWNER_EMAIL = 'lydia@dogboxx.org'


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'is_super_admin', sa.Boolean(),
            nullable=False, server_default=sa.false()
        ))

    # Grant owner-level access to the business owner.
    op.execute(
        sa.text("UPDATE users SET is_super_admin = TRUE WHERE email = :email")
        .bindparams(email=OWNER_EMAIL)
    )


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_super_admin')
