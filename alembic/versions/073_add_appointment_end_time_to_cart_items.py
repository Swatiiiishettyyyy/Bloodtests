"""Add appointment_end_time to cart_items for genetic test time-range slots

Revision ID: 073_add_appointment_end_time_to_cart_items
Revises: 072_add_order_number_counter
Create Date: 2026-04-16
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "073_add_appointment_end_time_to_cart_items"
down_revision: Union[str, None] = "072_add_order_number_counter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cart_items",
        sa.Column("appointment_end_time", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cart_items", "appointment_end_time")
