"""widen pickup_instructions to Text, add pickup_notes_photo

Revision ID: 15f7f5bab746
Revises: 8763c31f1b76
Create Date: 2026-07-19 10:16:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '15f7f5bab746'
down_revision = '8763c31f1b76'
branch_labels = None
depends_on = None


def upgrade():
    # String(1000) -> Text: pickup notes now hold Quill-authored HTML
    # (formatting markup eats into the old 1000-char budget fast).
    with op.batch_alter_table('dogs', schema=None) as batch_op:
        batch_op.alter_column(
            'pickup_instructions',
            existing_type=sa.String(length=1000),
            type_=sa.Text(),
            existing_nullable=True,
        )
        batch_op.add_column(sa.Column('pickup_notes_photo', sa.String(length=300), nullable=True))


def downgrade():
    with op.batch_alter_table('dogs', schema=None) as batch_op:
        batch_op.drop_column('pickup_notes_photo')
        batch_op.alter_column(
            'pickup_instructions',
            existing_type=sa.Text(),
            type_=sa.String(length=1000),
            existing_nullable=True,
        )
