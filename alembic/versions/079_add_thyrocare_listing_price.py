"""Add generated thyrocare_listing_price to thyrocare_products.

Revision ID: 079_add_thyrocare_listing_price
Revises: 078_add_thyrocare_product_detail_columns
Create Date: 2026-04-21

Column may already exist on some databases; upgrade is idempotent.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "079_add_thyrocare_listing_price"
down_revision: Union[str, None] = "078_add_thyrocare_product_detail_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(inspector, table: str, col: str) -> bool:
    return col in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "thyrocare_products" not in inspector.get_table_names():
        return

    t = "thyrocare_products"
    if not _col_exists(inspector, t, "thyrocare_listing_price"):
        op.add_column(
            t,
            sa.Column(
                "thyrocare_listing_price",
                sa.Integer(),
                sa.Computed("ROUND(thyrocare_price * 1.4)", persisted=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "thyrocare_products" not in inspector.get_table_names():
        return

    t = "thyrocare_products"
    if _col_exists(inspector, t, "thyrocare_listing_price"):
        op.drop_column(t, "thyrocare_listing_price")

