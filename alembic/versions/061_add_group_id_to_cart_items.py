"""add group_id to cart_items and category to thyrocare_products

Revision ID: 061_add_group_id_to_cart_items
Revises: 060_add_thyrocare_booking_status, 14079edbe3a6
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '061_add_group_id_to_cart_items'
down_revision = ('060_add_thyrocare_booking_status', '14079edbe3a6')
branch_labels = None
depends_on = None


def _col_exists(inspector, table, col):
    return col in {c['name'] for c in inspector.get_columns(table)}


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # --- cart_items: add group_id ---
    if 'cart_items' in inspector.get_table_names():
        if not _col_exists(inspector, 'cart_items', 'group_id'):
            op.add_column('cart_items', sa.Column('group_id', sa.String(100), nullable=True))
            op.create_index('ix_cart_items_group_id', 'cart_items', ['group_id'])

            # Back-fill existing rows with a unique value per row
            conn.execute(text("""
                UPDATE cart_items
                SET group_id = CONCAT('legacy_', id)
                WHERE group_id IS NULL
            """))

            # Now make it NOT NULL
            conn.execute(text("""
                ALTER TABLE cart_items
                MODIFY COLUMN group_id VARCHAR(100) NOT NULL
            """))

    # --- thyrocare_products: add missing category column ---
    if 'thyrocare_products' in inspector.get_table_names():
        if not _col_exists(inspector, 'thyrocare_products', 'category'):
            op.add_column('thyrocare_products', sa.Column('category', sa.String(200), nullable=True))
            op.create_index('ix_thyrocare_products_category', 'thyrocare_products', ['category'])

    # --- order_items: add missing scheduling/technician columns ---
    if 'order_items' in inspector.get_table_names():
        if not _col_exists(inspector, 'order_items', 'scheduled_date'):
            op.add_column('order_items', sa.Column('scheduled_date', sa.DateTime(timezone=True), nullable=True))
        if not _col_exists(inspector, 'order_items', 'technician_name'):
            op.add_column('order_items', sa.Column('technician_name', sa.String(100), nullable=True))
        if not _col_exists(inspector, 'order_items', 'technician_contact'):
            op.add_column('order_items', sa.Column('technician_contact', sa.String(20), nullable=True))


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'order_items' in inspector.get_table_names():
        for col in ['technician_contact', 'technician_name', 'scheduled_date']:
            if _col_exists(inspector, 'order_items', col):
                op.drop_column('order_items', col)

    if 'thyrocare_products' in inspector.get_table_names():
        if _col_exists(inspector, 'thyrocare_products', 'category'):
            try:
                op.drop_index('ix_thyrocare_products_category', table_name='thyrocare_products')
            except Exception:
                pass
            op.drop_column('thyrocare_products', 'category')

    if 'cart_items' in inspector.get_table_names():
        if _col_exists(inspector, 'cart_items', 'group_id'):
            try:
                op.drop_index('ix_cart_items_group_id', table_name='cart_items')
            except Exception:
                pass
            op.drop_column('cart_items', 'group_id')
