"""
Coupon model for blood test (Thyrocare) discount coupons.
Separate tables from genetic-product coupons.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Enum, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist
import enum


class BloodTestCouponType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class BloodTestCouponSpecialType(str, enum.Enum):
    STANDARD = "standard"
    CORPORATE = "corporate"   # 5% off, min ₹50
    FAMILY = "family"         # 10% off, min ₹100
    FOUNDER = "founder"       # 40% off, min ₹10


class BloodTestCouponStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"


class BloodTestCoupon(Base):
    __tablename__ = "blood_test_coupons"

    id = Column(Integer, primary_key=True, index=True)
    coupon_code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(String(500), nullable=True)

    discount_type = Column(Enum(BloodTestCouponType), nullable=False, default=BloodTestCouponType.PERCENTAGE)
    discount_value = Column(Float, nullable=False)

    min_order_amount = Column(Float, default=0.0, nullable=False)
    max_discount_amount = Column(Float, nullable=True)

    max_uses = Column(Integer, nullable=True)
    max_uses_per_user = Column(Integer, nullable=True, default=1)

    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)

    status = Column(Enum(BloodTestCouponStatus), nullable=False, default=BloodTestCouponStatus.ACTIVE, index=True)
    special_type = Column(Enum(BloodTestCouponSpecialType), nullable=False, default=BloodTestCouponSpecialType.STANDARD)

    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    cart_applications = relationship("BloodTestCartCoupon", back_populates="coupon", cascade="all, delete-orphan")
    usages = relationship("BloodTestCouponUsage", back_populates="coupon", cascade="all, delete-orphan")
    allowed_users = relationship("BloodTestCouponAllowedUser", back_populates="coupon", cascade="all, delete-orphan")


class BloodTestCartCoupon(Base):
    """Tracks coupon applied to a user's blood-test cart (temporary)."""
    __tablename__ = "blood_test_cart_coupons"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    coupon_id = Column(Integer, ForeignKey("blood_test_coupons.id", ondelete="CASCADE"), nullable=False, index=True)
    coupon_code = Column(String(50), nullable=False, index=True)
    discount_amount = Column(Float, nullable=False, default=0.0)
    applied_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    coupon = relationship("BloodTestCoupon", back_populates="cart_applications")


class BloodTestCouponUsage(Base):
    """Permanent record of a confirmed blood-test order that used a coupon."""
    __tablename__ = "blood_test_coupon_usages"

    id = Column(Integer, primary_key=True, index=True)
    coupon_id = Column(Integer, ForeignKey("blood_test_coupons.id", ondelete="CASCADE"), nullable=False, index=True)
    coupon_code = Column(String(50), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    order_id = Column(Integer, nullable=False, index=True)
    order_number = Column(String(50), nullable=False)
    discount_amount = Column(Float, nullable=False, default=0.0)
    used_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    coupon = relationship("BloodTestCoupon", back_populates="usages")


class BloodTestCouponAllowedUser(Base):
    """Restricts a blood-test coupon to specific users (by user_id or mobile)."""
    __tablename__ = "blood_test_coupon_allowed_users"

    id = Column(Integer, primary_key=True, index=True)
    coupon_id = Column(Integer, ForeignKey("blood_test_coupons.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    mobile = Column(String(100), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("coupon_id", "user_id", name="uq_bt_coupon_user_id"),
        UniqueConstraint("coupon_id", "mobile", name="uq_bt_coupon_mobile"),
        CheckConstraint("user_id IS NOT NULL OR mobile IS NOT NULL", name="ck_bt_coupon_allowed_users_not_null"),
    )

    coupon = relationship("BloodTestCoupon", back_populates="allowed_users")
