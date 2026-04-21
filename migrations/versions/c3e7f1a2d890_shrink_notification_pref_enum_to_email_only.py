"""shrink notification_pref enum to email only

Revision ID: c3e7f1a2d890
Revises: bb01daed9dde
Create Date: 2026-04-21 15:16:00.000000

WhatsApp notifications were removed from the product. This migration removes the
unused 'whatsapp' and 'both' values from the notification_pref enum. All existing
rows already use 'email' so no data migration is needed.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e7f1a2d890'
down_revision = 'bb01daed9dde'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != 'postgresql':
        return  # SQLite has no named enum types

    # Recreate the enum with only 'email'.
    # Must drop the column default first — it references the type and blocks DROP TYPE.
    op.execute("ALTER TABLE users ALTER COLUMN notification_preference DROP DEFAULT")
    op.execute("ALTER TABLE users ALTER COLUMN notification_preference TYPE VARCHAR(50)")
    op.execute("UPDATE users SET notification_preference = 'email' WHERE notification_preference != 'email'")
    op.execute("DROP TYPE notification_pref")
    op.execute("CREATE TYPE notification_pref AS ENUM ('email')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN notification_preference "
        "TYPE notification_pref USING notification_preference::notification_pref"
    )
    op.execute("ALTER TABLE users ALTER COLUMN notification_preference SET DEFAULT 'email'::notification_pref")


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name != 'postgresql':
        return

    op.execute("ALTER TABLE users ALTER COLUMN notification_preference DROP DEFAULT")
    op.execute("ALTER TABLE users ALTER COLUMN notification_preference TYPE VARCHAR(50)")
    op.execute("DROP TYPE notification_pref")
    op.execute("CREATE TYPE notification_pref AS ENUM ('email', 'whatsapp', 'both')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN notification_preference "
        "TYPE notification_pref USING notification_preference::notification_pref"
    )
    op.execute("ALTER TABLE users ALTER COLUMN notification_preference SET DEFAULT 'email'::notification_pref")
