"""add last_seen_at to push_subscriptions

Revision ID: 11d45fd682ab
Revises: 393f7f0c9da4
Create Date: 2026-08-07 13:57:28.178079

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '11d45fd682ab'
down_revision = '393f7f0c9da4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_seen_at', sa.DateTime(), nullable=True))

    # Backfill: existing rows predate this liveness signal (M31), so seed it
    # from whichever of created_at/updated_at is more recent — the best
    # available proxy for "last known good" (updated_at only reflects key
    # rotation, not real liveness, but for legacy rows it's what we have).
    # Leaving it NULL would make every pre-existing row — including the
    # already-known-stale ones — permanently invisible to the `< cutoff`
    # sweep query, since SQL NULL comparisons never match. Row-by-row to
    # stay portable across SQLite and Postgres (matching 393f7f0c9da4's
    # backfill pattern) rather than Postgres-only GREATEST().
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        'SELECT id, created_at, updated_at FROM push_subscriptions'
    )).fetchall()
    for sub_id, created_at, updated_at in rows:
        last_seen = max(created_at, updated_at) if updated_at else created_at
        conn.execute(
            sa.text('UPDATE push_subscriptions SET last_seen_at = :ts WHERE id = :id'),
            {'ts': last_seen, 'id': sub_id},
        )


def downgrade():
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.drop_column('last_seen_at')
