"""Add WalkerUnavailability model

Revision ID: 2a8c1ecbb7c8
Revises: d554aaabf9ca
Create Date: 2026-02-25 15:03:08.876184

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2a8c1ecbb7c8'
down_revision = 'd554aaabf9ca'
branch_labels = None
depends_on = None

# Reference existing enum - don't create it
schedule_slot = postgresql.ENUM('Morning', 'Afternoon', name='schedule_slot', create_type=False)


def upgrade():
    op.create_table('walker_unavailabilities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('walker_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('slot', schedule_slot, nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['walker_id'], ['walkers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('walker_id', 'date', 'slot', name='uq_walker_date_slot')
    )
    with op.batch_alter_table('walker_unavailabilities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_walker_unavailabilities_walker_id'), ['walker_id'], unique=False)


def downgrade():
    with op.batch_alter_table('walker_unavailabilities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_walker_unavailabilities_walker_id'))

    op.drop_table('walker_unavailabilities')
