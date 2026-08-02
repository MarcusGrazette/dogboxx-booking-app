"""add walker_id to booking_status_changes

Revision ID: 393f7f0c9da4
Revises: a7e2c9f14d8b
Create Date: 2026-08-02 11:18:15.967012

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '393f7f0c9da4'
down_revision = 'a7e2c9f14d8b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('booking_status_changes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('walker_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f('fk_booking_status_changes_walker_id_walkers'),
            'walkers', ['walker_id'], ['id'],
        )

    # Backfill: pre-existing rows predate the snapshot, so best-effort fill
    # from the booking's current walker_id — same accuracy the activity feed
    # already had before this migration (a live lookup), just frozen in
    # place. Every row written from here on is an exact point-in-time
    # snapshot instead. Row-by-row (not UPDATE...FROM) to stay portable
    # across SQLite and Postgres, matching a7e2c9f14d8b's backfill pattern.
    conn = op.get_bind()
    rows = conn.execute(sa.text('''
        SELECT bsc.id, b.walker_id
        FROM booking_status_changes bsc
        JOIN bookings b ON b.id = bsc.booking_id
        WHERE b.walker_id IS NOT NULL
    ''')).fetchall()
    for bsc_id, walker_id in rows:
        conn.execute(
            sa.text('UPDATE booking_status_changes SET walker_id = :wid WHERE id = :id'),
            {'wid': walker_id, 'id': bsc_id},
        )


def downgrade():
    with op.batch_alter_table('booking_status_changes', schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f('fk_booking_status_changes_walker_id_walkers'), type_='foreignkey')
        batch_op.drop_column('walker_id')
