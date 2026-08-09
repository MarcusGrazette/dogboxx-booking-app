"""add session_token to users

Revision ID: ad12a36f7ce0
Revises: 11d45fd682ab
Create Date: 2026-08-09 08:41:11.881302

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ad12a36f7ce0'
down_revision = '11d45fd682ab'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('session_token', sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column('users', 'session_token')
