"""add thyrocare booking status to order_items

Revision ID: 060_add_thyrocare_booking_status
Revises: 059_add_lab_report_url_to_order_items
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = '060_add_thyrocare_booking_status'
down_revision = '031_thyrocare_products'
branch_labels = None
depends_on = None


def _col_exists(inspector, table, col):
    return col in {c['name'] for c in inspector.get_columns(table)}


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not _col_exists(inspector, 'order_items', 'thyrocare_booking_status'):
        op.add_column('order_items', sa.Column('thyrocare_booking_status', sa.String(20), nullable=True))

    if not _col_exists(inspector, 'order_items', 'thyrocare_booking_error'):
        op.add_column('order_items', sa.Column('thyrocare_booking_error', sa.String(500), nullable=True))


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    for col in ['thyrocare_booking_error', 'thyrocare_booking_status']:
        if _col_exists(inspector, 'order_items', col):
            op.drop_column('order_items', col)
