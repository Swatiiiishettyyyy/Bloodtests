"""Add blood test coupon fields to orders.

Revision ID: 086_add_blood_test_coupon_fields_to_orders
Revises: 085_make_thyrocare_price_not_null
Create Date: 2026-05-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "086_add_blood_test_coupon_fields_to_orders"
down_revision: Union[str, None] = "085_make_thyrocare_price_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {column["name"] for column in inspector.get_columns("orders")}

    if "blood_test_coupon_code" not in columns:
        op.add_column("orders", sa.Column("blood_test_coupon_code", sa.String(50), nullable=True))

    if "blood_test_coupon_amount" not in columns:
        op.add_column(
            "orders",
            sa.Column("blood_test_coupon_amount", sa.Float(), nullable=True, server_default="0"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {column["name"] for column in inspector.get_columns("orders")}

    if "blood_test_coupon_amount" in columns:
        op.drop_column("orders", "blood_test_coupon_amount")

    if "blood_test_coupon_code" in columns:
        op.drop_column("orders", "blood_test_coupon_code")
