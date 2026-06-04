"""Drop unused walk_events table (dead WalkEvent model)

Revision ID: b40f4de664d4
Revises: fa6d5571aa08
Create Date: 2026-05-29 17:36:07.925754

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b40f4de664d4'
down_revision = 'fa6d5571aa08'
branch_labels = None
depends_on = None


def upgrade():
    # The walk_events table was created in 421fe98dd8f0 for an anticipated
    # pickup/drop-off recording feature that was never built (the WalkEvent
    # model had zero writes). Drop the empty table and its orphaned PG enum.
    op.drop_table('walk_events')
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # drop_table leaves the enum type behind on Postgres; remove it too.
        sa.Enum(name='walk_event_type').drop(bind, checkfirst=True)


def downgrade():
    # Recreate exactly as defined in 421fe98dd8f0. On Postgres create_table
    # re-creates the walk_event_type enum automatically.
    op.create_table(
        'walk_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.Enum('en_route', 'picked_up', 'dropped_off',
                                         name='walk_event_type'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
