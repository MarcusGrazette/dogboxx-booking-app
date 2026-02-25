"""Change dog birth_year_month to date_of_birth

Revision ID: 37084a06d693
Revises: 2a8c1ecbb7c8
Create Date: 2026-02-25 17:21:51.683357

"""
from alembic import op
import sqlalchemy as sa


revision = '37084a06d693'
down_revision = '2a8c1ecbb7c8'
branch_labels = None
depends_on = None


def upgrade():
    # Add new column
    op.add_column('dogs', sa.Column('date_of_birth', sa.Date(), nullable=True))

    # Convert existing birth_year_month (e.g. 202301 = Jan 2023) to date (1st of that month)
    op.execute("""
        UPDATE dogs
        SET date_of_birth = make_date(
            (birth_year_month / 100)::int,
            (birth_year_month % 100)::int,
            1
        )
        WHERE birth_year_month IS NOT NULL
    """)

    # Drop old column
    op.drop_column('dogs', 'birth_year_month')


def downgrade():
    op.add_column('dogs', sa.Column('birth_year_month', sa.NUMERIC(), nullable=True))

    op.execute("""
        UPDATE dogs
        SET birth_year_month = EXTRACT(YEAR FROM date_of_birth) * 100 + EXTRACT(MONTH FROM date_of_birth)
        WHERE date_of_birth IS NOT NULL
    """)

    op.drop_column('dogs', 'date_of_birth')
