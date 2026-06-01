"""add_created_by_id_to_availability_tables

Revision ID: 8f826da874ff
Revises: 6913631b986e
Create Date: 2026-06-01 15:00:12.819590

Adds created_by_id (nullable FK to users) to walker_unavailabilities and
walker_adhoc_availability so the activity feed can distinguish admin-on-behalf
actions from walker self-service (§9.2 Session 4 schema changes).

Existing rows get NULL (interpreted as "self-service / unknown").
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f826da874ff'
down_revision = '6913631b986e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('walker_unavailabilities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_walker_unavail_created_by',
            'users', ['created_by_id'], ['id'],
        )
        batch_op.create_index('ix_walker_unavail_created_by_id', ['created_by_id'])

    with op.batch_alter_table('walker_adhoc_availability', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_walker_adhoc_created_by',
            'users', ['created_by_id'], ['id'],
        )
        batch_op.create_index('ix_walker_adhoc_created_by_id', ['created_by_id'])


def downgrade():
    with op.batch_alter_table('walker_adhoc_availability', schema=None) as batch_op:
        batch_op.drop_index('ix_walker_adhoc_created_by_id')
        batch_op.drop_constraint('fk_walker_adhoc_created_by', type_='foreignkey')
        batch_op.drop_column('created_by_id')

    with op.batch_alter_table('walker_unavailabilities', schema=None) as batch_op:
        batch_op.drop_index('ix_walker_unavail_created_by_id')
        batch_op.drop_constraint('fk_walker_unavail_created_by', type_='foreignkey')
        batch_op.drop_column('created_by_id')
