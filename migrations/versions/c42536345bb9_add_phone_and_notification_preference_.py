"""Add phone and notification_preference to users

Revision ID: c42536345bb9
Revises: dca48e3a72af
Create Date: 2026-02-21 16:42:27.410747

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c42536345bb9'
down_revision = 'dca48e3a72af'
branch_labels = None
depends_on = None


def upgrade():
    notification_pref = sa.Enum('email', 'whatsapp', 'both', name='notification_pref')
    notification_pref.create(op.get_bind(), checkfirst=True)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('notification_preference', notification_pref, nullable=False, server_default='email'))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('notification_preference')
        batch_op.drop_column('phone')

    sa.Enum(name='notification_pref').drop(op.get_bind(), checkfirst=True)
