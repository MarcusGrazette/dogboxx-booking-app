"""add admin_action_logs table

Revision ID: a780a9cae10d
Revises: 3faed1394d05
Create Date: 2026-08-28 10:52:07.861882

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a780a9cae10d'
down_revision = '3faed1394d05'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('admin_action_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_admin_action_logs_entity_type'), 'admin_action_logs', ['entity_type'], unique=False)
    op.create_index(op.f('ix_admin_action_logs_entity_id'), 'admin_action_logs', ['entity_id'], unique=False)
    op.create_index(op.f('ix_admin_action_logs_created_at'), 'admin_action_logs', ['created_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_admin_action_logs_created_at'), table_name='admin_action_logs')
    op.drop_index(op.f('ix_admin_action_logs_entity_id'), table_name='admin_action_logs')
    op.drop_index(op.f('ix_admin_action_logs_entity_type'), table_name='admin_action_logs')
    op.drop_table('admin_action_logs')
