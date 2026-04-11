"""Cleanup legacy orders columns and add thyrocare booking fields

Revision ID: 062_cleanup_orders_thyrocare_columns
Revises: 061_add_group_id_to_cart_items
Create Date: 2026-04-09

Drops legacy unmapped columns from orders table:
  - thyrocare_order_id
  - provider
  - thyrocare_provider_status
  - thyrocare_price_breakup
  - provider_metadata

Adds clean mapped columns:
  - thyrocare_order_id  (VARCHAR 100, nullable) — Thyrocare orderId returned after booking
  - thyrocare_booking_status  (VARCHAR 20, nullable) — BOOKED / FAILED / null
"""
from alembic import op
import sqlalchemy as sa

revision = '062_cleanup_orders_thyrocare_columns'
down_revision = ('061_add_group_id_to_cart_items', '057_add_emi_to_products')
branch_labels = None
depends_on = None

LEGACY_COLS = ['provider', 'thyrocare_provider_status', 'thyrocare_price_breakup', 'provider_metadata']
NEW_COLS = [
    ('thyrocare_order_id', sa.String(100)),
    ('thyrocare_booking_status', sa.String(20)),
]


def _col_exists(inspector, table, col):
    return col in {c['name'] for c in inspector.get_columns(table)}


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'orders' not in inspector.get_table_names():
        return

    # Drop legacy columns if they exist
    for col in LEGACY_COLS:
        if _col_exists(inspector, 'orders', col):
            op.drop_column('orders', col)

    # Also drop thyrocare_order_id if it exists (we'll re-add it cleanly)
    if _col_exists(inspector, 'orders', 'thyrocare_order_id'):
        op.drop_column('orders', 'thyrocare_order_id')

    # Add clean columns
    op.add_column('orders', sa.Column('thyrocare_order_id', sa.String(100), nullable=True))
    op.add_column('orders', sa.Column('thyrocare_booking_status', sa.String(20), nullable=True))
    op.create_index('ix_orders_thyrocare_order_id', 'orders', ['thyrocare_order_id'])
    op.create_index('ix_orders_thyrocare_booking_status', 'orders', ['thyrocare_booking_status'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'orders' not in inspector.get_table_names():
        return

    # Drop the new columns
    for col, _ in NEW_COLS:
        if _col_exists(inspector, 'orders', col):
            try:
                op.drop_index(f'ix_orders_{col}', table_name='orders')
            except Exception:
                pass
            op.drop_column('orders', col)

    # Restore legacy columns
    op.add_column('orders', sa.Column('provider', sa.String(100), nullable=True))
    op.add_column('orders', sa.Column('thyrocare_provider_status', sa.String(50), nullable=True))
    op.add_column('orders', sa.Column('thyrocare_price_breakup', sa.JSON(), nullable=True))
    op.add_column('orders', sa.Column('provider_metadata', sa.JSON(), nullable=True))
