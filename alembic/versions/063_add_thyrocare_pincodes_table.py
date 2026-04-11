"""Add thyrocare_pincodes table

Revision ID: 063_add_thyrocare_pincodes_table
Revises: 062_cleanup_orders_thyrocare_columns
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa

revision = '063_add_thyrocare_pincodes_table'
down_revision = '062_cleanup_orders_thyrocare_columns'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'thyrocare_pincodes',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('pincode', sa.String(10), unique=True, nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_thyrocare_pincodes_is_active', 'thyrocare_pincodes', ['is_active'])


def downgrade():
    op.drop_table('thyrocare_pincodes')
