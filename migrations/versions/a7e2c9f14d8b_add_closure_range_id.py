"""add range_id to closures for date-range closures

Revision ID: a7e2c9f14d8b
Revises: 15f7f5bab746
Create Date: 2026-07-30 12:00:00.000000

"""
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7e2c9f14d8b'
down_revision = '15f7f5bab746'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('closures', schema=None) as batch_op:
        batch_op.add_column(sa.Column('range_id', sa.String(length=32), nullable=True))
        batch_op.create_index(batch_op.f('ix_closures_range_id'), ['range_id'], unique=False)

    # Backfill: every pre-existing row predates date-range closures, so each is
    # its own one-row group — give each a distinct range_id.
    conn = op.get_bind()
    closures = conn.execute(sa.text('SELECT id FROM closures WHERE range_id IS NULL')).fetchall()
    for (closure_id,) in closures:
        conn.execute(
            sa.text('UPDATE closures SET range_id = :rid WHERE id = :id'),
            {'rid': uuid.uuid4().hex, 'id': closure_id},
        )

    with op.batch_alter_table('closures', schema=None) as batch_op:
        batch_op.alter_column('range_id', existing_type=sa.String(length=32), nullable=False)


def downgrade():
    with op.batch_alter_table('closures', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_closures_range_id'))
        batch_op.drop_column('range_id')
