"""add closure table

Revision ID: c3f1a8b29e4d
Revises: faf8e45aafc5
Create Date: 2026-05-08 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3f1a8b29e4d'
down_revision = '366907dd88dc'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('closures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_closures_date'), 'closures', ['date'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_closures_date'), table_name='closures')
    op.drop_table('closures')
