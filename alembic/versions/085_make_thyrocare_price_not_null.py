"""Make thyrocare_price non-nullable on thyrocare_products.

Revision ID: 085_make_thyrocare_price_not_null
Revises: 084_add_blood_test_coupon_tables
Create Date: 2026-05-03
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "085_make_thyrocare_price_not_null"
down_revision: Union[str, None] = "084_add_blood_test_coupon_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE thyrocare_products SET thyrocare_price = 0.0 WHERE thyrocare_price IS NULL")
    op.alter_column(
        "thyrocare_products",
        "thyrocare_price",
        existing_type=sa.Float(),
        nullable=False,
        server_default="0.0",
    )


def downgrade() -> None:
    op.alter_column(
        "thyrocare_products",
        "thyrocare_price",
        existing_type=sa.Float(),
        nullable=True,
        server_default=None,
    )
