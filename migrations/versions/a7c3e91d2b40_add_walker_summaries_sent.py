"""add walker_summaries_sent table

Revision ID: a7c3e91d2b40
Revises: 0f9a3eb9bc78
Create Date: 2026-10-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c3e91d2b40'
down_revision = '0f9a3eb9bc78'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('walker_summaries_sent',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('walker_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['walker_id'], ['walkers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('walker_id', 'date', 'kind', name='uq_walker_summary_sent')
    )


def downgrade():
    op.drop_table('walker_summaries_sent')
