"""
Coupon model for managing discount coupons.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, func, Enum, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist
import enum


class CouponType(str, enum.Enum):
    """Coupon discount types"""
    PERCENTAGE = "percentage"  # Discount as percentage (e.g., 10% off)
    FIXED = "fixed"  # Fixed amount discount (e.g., ₹100 off)


class CouponStatus(str, enum.Enum):
    """Coupon status"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"


class Coupon(Base):
    """
    Coupon table for managing discount coupons.
    """
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    coupon_code = Column(String(50), unique=True, nullable=False, index=True)  # Unique coupon code
    description = Column(String(500), nullable=True)  # Coupon description
    
    # Note: user_id removed - all coupons are now applicable to all users
    # Usage limits are tracked via max_uses and cart_coupons table
    
    # Discount details
    discount_type = Column(Enum(CouponType), nullable=False, default=CouponType.PERCENTAGE)
    discount_value = Column(Float, nullable=False)  # Percentage (0-100) or fixed amount
    
    # Minimum order amount to apply coupon
    min_order_amount = Column(Float, default=0.0, nullable=False)
    
    # Maximum discount cap (for percentage coupons)
    max_discount_amount = Column(Float, nullable=True)  # Optional cap on discount
    
    # Usage limits
    max_uses = Column(Integer, nullable=True)  # Total uses allowed (None = unlimited, not required)
    # max_uses_per_user removed - not needed
    
    # Validity period
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    
    # Status
    status = Column(Enum(CouponStatus), nullable=False, default=CouponStatus.ACTIVE, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)
    
    # Relationships
    cart_applications = relationship("CartCoupon", back_populates="coupon", cascade="all, delete-orphan")


class CartCoupon(Base):
    """
    Tracks coupon applications to carts.
    Links coupons with cart items and stores the applied discount amount.
    """
    __tablename__ = "cart_coupons"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)  # User who applied the coupon
    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False, index=True)  # Applied coupon
    coupon_code = Column(String(50), nullable=False, index=True)  # Coupon code for quick reference
    
    # Discount amount applied (calculated at time of application)
    discount_amount = Column(Float, nullable=False, default=0.0)
    
    # Cart reference (can be null if coupon is applied but cart is cleared)
    # We track by user_id and coupon_code for flexibility
    applied_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    
    # Relationships
    coupon = relationship("Coupon", back_populates="cart_applications")

