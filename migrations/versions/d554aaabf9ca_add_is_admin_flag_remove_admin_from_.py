"""Add is_admin flag, remove admin from user_roles enum

Revision ID: d554aaabf9ca
Revises: cecfaed20a55
Create Date: 2026-02-25 12:44:23.125414

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd554aaabf9ca'
down_revision = 'cecfaed20a55'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add is_admin column (default False)
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='false'))

    # 2. Migrate existing admin users: set is_admin=True, role='walker'
    op.execute("UPDATE users SET is_admin = true WHERE role = 'admin'")
    op.execute("UPDATE users SET role = 'walker' WHERE role = 'admin'")

    # 3. Replace the enum type (Postgres requires recreating it)
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(10)")
    op.execute("DROP TYPE IF EXISTS user_roles")
    op.execute("CREATE TYPE user_roles AS ENUM ('client', 'walker')")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE user_roles USING role::user_roles")


def downgrade():
    # 1. Restore 'admin' to the enum
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(10)")
    op.execute("DROP TYPE IF EXISTS user_roles")
    op.execute("CREATE TYPE user_roles AS ENUM ('client', 'walker', 'admin')")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE user_roles USING role::user_roles")

    # 2. Migrate back: is_admin users become role='admin'
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin = true")

    # 3. Drop the column
    op.drop_column('users', 'is_admin')
