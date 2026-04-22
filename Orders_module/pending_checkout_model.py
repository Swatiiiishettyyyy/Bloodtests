"""
PendingCheckout — a lightweight record created when the user initiates payment.

An Order row (with a real sequential order number) is created ONLY after
payment is confirmed (via verify-payment or Razorpay webhook).

This prevents:
  - Stale PENDING_PAYMENT order rows for abandoned checkouts
  - Wasted / gapped order numbers in the audit trail
  - Polluted orders table with unconfirmed attempts
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist
from datetime import timedelta


def _expires_at():
    return now_ist() + timedelta(hours=24)


class PendingCheckout(Base):
    __tablename__ = "pending_checkouts"

    id = Column(Integer, primary_key=True, index=True)

    # The user who initiated checkout
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Razorpay order created at checkout initiation — unique per checkout attempt
    razorpay_order_id = Column(String(100), unique=True, nullable=False, index=True)

    # Amount charged (INR)
    amount = Column(Float, nullable=False)

    # Snapshot of cart at initiation — list of CartItem IDs
    cart_item_ids = Column(JSON, nullable=False)

    # Primary address used for the order
    address_id = Column(Integer, nullable=True)

    # Member profile that was active during checkout (optional)
    placed_by_member_id = Column(Integer, nullable=True)

    # Coupon applied at checkout time
    coupon_code = Column(String(50), nullable=True)
    coupon_discount = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    # Auto-expire after 24 h — stale rows can be safely purged without any order-number impact
    expires_at = Column(DateTime(timezone=True), default=_expires_at, nullable=False)

    # Set ONLY after payment is confirmed and the Order row has been created.
    # While this is NULL the checkout is still pending / abandoned.
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)

    # Relationships
    user = relationship("User")
    order = relationship("Order")
