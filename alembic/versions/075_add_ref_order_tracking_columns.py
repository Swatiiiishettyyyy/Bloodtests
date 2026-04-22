"""Add ref_order_no, thyrocare_product_id, order_item_ids to thyrocare_order_tracking

Revision ID: 075_add_ref_order_tracking_columns
Revises: 074_add_pending_checkout_table
Create Date: 2026-04-16

Why: refOrderNo sent to Thyrocare is now derived from the internal order number
     (e.g. ORD-2024-00123_1, ORD-2024-00123_2) so we can trace back any Thyrocare
     communication to the exact order and product group. Storing thyrocare_product_id
     and order_item_ids gives complete webhook→order-item mapping without extra queries.
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "075_add_ref_order_tracking_columns"
down_revision: Union[str, None] = "074_add_pending_checkout_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "thyrocare_order_tracking",
        sa.Column("ref_order_no", sa.String(100), nullable=True),
    )
    op.add_column(
        "thyrocare_order_tracking",
        sa.Column("thyrocare_product_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "thyrocare_order_tracking",
        sa.Column("order_item_ids", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_thyrocare_order_tracking_ref_order_no",
        "thyrocare_order_tracking",
        ["ref_order_no"],
    )
    op.create_index(
        "ix_thyrocare_order_tracking_thyrocare_product_id",
        "thyrocare_order_tracking",
        ["thyrocare_product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_thyrocare_order_tracking_thyrocare_product_id", "thyrocare_order_tracking")
    op.drop_index("ix_thyrocare_order_tracking_ref_order_no", "thyrocare_order_tracking")
    op.drop_column("thyrocare_order_tracking", "order_item_ids")
    op.drop_column("thyrocare_order_tracking", "thyrocare_product_id")
    op.drop_column("thyrocare_order_tracking", "ref_order_no")
