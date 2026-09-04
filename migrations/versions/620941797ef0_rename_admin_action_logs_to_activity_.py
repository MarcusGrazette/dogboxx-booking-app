"""rename admin_action_logs to activity_logs

Pure rename — AdminActionLog becomes ActivityLog (app/models.py) ahead of a
future PR that adds client-authored rows (FEATURES.md #47), at which point
the "admin" framing in the old name would be wrong. Cheap ALTER TABLE/INDEX
RENAME, no data touched, no table rewrite.

Revision ID: 620941797ef0
Revises: a780a9cae10d
Create Date: 2026-09-02 18:09:31.463745

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '620941797ef0'
down_revision = 'a780a9cae10d'
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table('admin_action_logs', 'activity_logs')
    op.execute('ALTER INDEX ix_admin_action_logs_entity_type RENAME TO ix_activity_logs_entity_type')
    op.execute('ALTER INDEX ix_admin_action_logs_entity_id RENAME TO ix_activity_logs_entity_id')
    op.execute('ALTER INDEX ix_admin_action_logs_created_at RENAME TO ix_activity_logs_created_at')
    # Postgres doesn't rename a SERIAL column's owned sequence along with the
    # table — do it explicitly so it doesn't stay named after the old table
    # forever (cosmetic only, but flask db check's autogenerate diff ignores
    # sequence names, so nothing catches this if skipped).
    op.execute('ALTER SEQUENCE admin_action_logs_id_seq RENAME TO activity_logs_id_seq')


def downgrade():
    op.execute('ALTER SEQUENCE activity_logs_id_seq RENAME TO admin_action_logs_id_seq')
    op.execute('ALTER INDEX ix_activity_logs_entity_type RENAME TO ix_admin_action_logs_entity_type')
    op.execute('ALTER INDEX ix_activity_logs_entity_id RENAME TO ix_admin_action_logs_entity_id')
    op.execute('ALTER INDEX ix_activity_logs_created_at RENAME TO ix_admin_action_logs_created_at')
    op.rename_table('activity_logs', 'admin_action_logs')
