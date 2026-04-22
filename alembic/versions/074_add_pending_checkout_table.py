"""Add pending_checkouts table for deferred order creation

Revision ID: 074_add_pending_checkout_table
Revises: 073_add_appointment_end_time_to_cart_items
Create Date: 2026-04-16

Why: Order rows (with real sequential ORD... numbers) are now created ONLY
     after payment is confirmed. PendingCheckout holds the Razorpay order ID
     and cart snapshot during the payment window (up to 24 h TTL).
     Abandoned checkouts expire cleanly without polluting the orders table.
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "074_add_pending_checkout_table"
down_revision: Union[str, None] = "073_add_appointment_end_time_to_cart_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_checkouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("razorpay_order_id", sa.String(100), unique=True, nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("cart_item_ids", sa.JSON(), nullable=False),
        sa.Column("address_id", sa.Integer(), nullable=True),
        sa.Column("placed_by_member_id", sa.Integer(), nullable=True),
        sa.Column("coupon_code", sa.String(50), nullable=True),
        sa.Column("coupon_discount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_pending_checkouts_user_id", "pending_checkouts", ["user_id"])
    op.create_index("ix_pending_checkouts_razorpay_order_id", "pending_checkouts", ["razorpay_order_id"], unique=True)
    op.create_index("ix_pending_checkouts_order_id", "pending_checkouts", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_checkouts_order_id", "pending_checkouts")
    op.drop_index("ix_pending_checkouts_razorpay_order_id", "pending_checkouts")
    op.drop_index("ix_pending_checkouts_user_id", "pending_checkouts")
    op.drop_table("pending_checkouts")
