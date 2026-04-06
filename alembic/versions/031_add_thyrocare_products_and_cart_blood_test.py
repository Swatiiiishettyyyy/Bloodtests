"""add thyrocare products and cart blood test support

Revision ID: 031_thyrocare_products
Revises: 056_add_coupon_allowed_users, 054_add_enquiry_requests
Create Date: 2026-04-06
"""
from alembic import op
import sqlalchemy as sa

revision = '031_thyrocare_products'
down_revision = ('056_add_coupon_allowed_users', '054_add_enquiry_requests')
branch_labels = None
depends_on = None


def _col_exists(inspector, table, col):
    return col in {c['name'] for c in inspector.get_columns(table)}


def _table_exists(inspector, table):
    return table in inspector.get_table_names()


def _index_exists(inspector, table, index):
    return index in {i['name'] for i in inspector.get_indexes(table)}


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # --- thyrocare_products ---
    if not _table_exists(inspector, 'thyrocare_products'):
        op.create_table(
            'thyrocare_products',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('thyrocare_id', sa.String(50), nullable=False, unique=True),
            sa.Column('name', sa.String(300), nullable=False),
            sa.Column('type', sa.String(50), nullable=False),
            sa.Column('no_of_tests_included', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('listing_price', sa.Float(), nullable=False, server_default='0'),
            sa.Column('selling_price', sa.Float(), nullable=False, server_default='0'),
            sa.Column('discount_percentage', sa.Float(), nullable=False, server_default='0'),
            sa.Column('notational_incentive', sa.Float(), nullable=False, server_default='0'),
            sa.Column('beneficiaries_min', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('beneficiaries_max', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('beneficiaries_multiple', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('is_fasting_required', sa.Boolean(), nullable=True),
            sa.Column('is_home_collectible', sa.Boolean(), nullable=True),
            sa.Column('about', sa.Text(), nullable=True),
            sa.Column('short_description', sa.String(500), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )

    # --- thyrocare_test_parameters ---
    if not _table_exists(inspector, 'thyrocare_test_parameters'):
        op.create_table(
            'thyrocare_test_parameters',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('thyrocare_product_id', sa.Integer(),
                      sa.ForeignKey('thyrocare_products.id', ondelete='CASCADE'),
                      nullable=False),
            sa.Column('name', sa.String(300), nullable=False),
            sa.Column('group_name', sa.String(200), nullable=True),
        )

    # --- cart_items: new columns ---
    # Re-inspect after potential table creation
    inspector = sa.inspect(conn)

    if not _col_exists(inspector, 'cart_items', 'product_type'):
        op.add_column('cart_items', sa.Column('product_type', sa.String(20), nullable=False, server_default='genetic'))

    if not _col_exists(inspector, 'cart_items', 'thyrocare_product_id'):
        op.add_column('cart_items', sa.Column('thyrocare_product_id', sa.Integer(),
                      sa.ForeignKey('thyrocare_products.id'), nullable=True))

    if not _col_exists(inspector, 'cart_items', 'appointment_date'):
        op.add_column('cart_items', sa.Column('appointment_date', sa.Date(), nullable=True))

    if not _col_exists(inspector, 'cart_items', 'appointment_start_time'):
        op.add_column('cart_items', sa.Column('appointment_start_time', sa.String(20), nullable=True))

    # Make product_id nullable
    op.alter_column('cart_items', 'product_id', existing_type=sa.Integer(), nullable=True)

    # --- order_items: new columns ---
    if not _col_exists(inspector, 'order_items', 'thyrocare_order_id'):
        op.add_column('order_items', sa.Column('thyrocare_order_id', sa.String(100), nullable=True))

    if not _col_exists(inspector, 'order_items', 'thyrocare_product_id'):
        op.add_column('order_items', sa.Column('thyrocare_product_id', sa.Integer(),
                      sa.ForeignKey('thyrocare_products.id'), nullable=True))


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    for col in ['thyrocare_product_id', 'thyrocare_order_id']:
        if _col_exists(inspector, 'order_items', col):
            op.drop_column('order_items', col)

    for col in ['appointment_start_time', 'appointment_date', 'thyrocare_product_id', 'product_type']:
        if _col_exists(inspector, 'cart_items', col):
            op.drop_column('cart_items', col)

    op.alter_column('cart_items', 'product_id', existing_type=sa.Integer(), nullable=False)

    if _table_exists(inspector, 'thyrocare_test_parameters'):
        op.drop_table('thyrocare_test_parameters')
    if _table_exists(inspector, 'thyrocare_products'):
        op.drop_table('thyrocare_products')

