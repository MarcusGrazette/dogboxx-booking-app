"""Widen clients.street_address to TEXT

Three address lines of up to 200 characters each are joined with newlines into
this one column, which was VARCHAR(200) — two ordinary lines could overflow it
and 500 the onboarding/admin client forms. The per-line form validators are now
the only bound. VARCHAR -> TEXT is a catalog-only change on Postgres (no table
rewrite).

Revision ID: c3e7a1f94d52
Revises: b8d4f2a61c37
Create Date: 2026-11-01 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e7a1f94d52'
down_revision = 'b8d4f2a61c37'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.alter_column('street_address',
                              existing_type=sa.String(length=200),
                              type_=sa.Text(),
                              existing_nullable=True)


def downgrade():
    # Truncates any address longer than 200 characters — unavoidable when
    # narrowing back.
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.alter_column('street_address',
                              existing_type=sa.Text(),
                              type_=sa.String(length=200),
                              existing_nullable=True,
                              postgresql_using='left(street_address, 200)')
