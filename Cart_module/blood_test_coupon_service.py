"""
Coupon service for blood-test (Thyrocare) discount coupons.
Mirror of coupon_service.py but operates on blood_test_coupons tables
and calculates subtotal from thyrocare_price.
"""
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional, Tuple
import logging

from .BloodTestCoupon_model import (
    BloodTestCoupon,
    BloodTestCartCoupon,
    BloodTestCouponUsage,
    BloodTestCouponType,
    BloodTestCouponStatus,
    BloodTestCouponAllowedUser,
)
from Login_module.Utils.datetime_utils import now_ist

logger = logging.getLogger(__name__)


def _get_bt_subtotal(db: Session, user_id: int) -> float:
    """Calculate subtotal from blood_test cart items via thyrocare_price."""
    from .Cart_model import CartItem
    from Thyrocare_module.Thyrocare_model import ThyrocareProduct
    bt_items = (
        db.query(CartItem)
        .options(joinedload(CartItem.thyrocare_product))
        .filter(
            CartItem.user_id == user_id,
            CartItem.is_deleted == False,
            CartItem.product_type == "blood_test",
        )
        .all()
    )
    return sum(
        i.quantity * (i.thyrocare_product.selling_price or 0.0)
        for i in bt_items
        if i.thyrocare_product
    )


def is_user_allowed_for_coupon(db: Session, coupon: BloodTestCoupon, user_id: int, mobile: str) -> bool:
    """Returns True if the coupon is unrestricted OR the user is on the allowlist."""
    from sqlalchemy import or_
    count = (
        db.query(func.count(BloodTestCouponAllowedUser.id))
        .filter(BloodTestCouponAllowedUser.coupon_id == coupon.id)
        .scalar()
        or 0
    )
    if count == 0:
        return True
    match = (
        db.query(BloodTestCouponAllowedUser)
        .filter(
            BloodTestCouponAllowedUser.coupon_id == coupon.id,
            or_(
                BloodTestCouponAllowedUser.user_id == user_id,
                BloodTestCouponAllowedUser.mobile == mobile,
            ),
        )
        .first()
    )
    return match is not None


def validate_and_calculate_discount(
    db: Session,
    coupon_code: str,
    user_id: int,
    subtotal_amount: float,
) -> Tuple[Optional[BloodTestCoupon], float, str]:
    """Validate blood-test coupon and calculate discount amount."""
    if not coupon_code:
        return None, 0.0, ""

    normalized_code = coupon_code.upper().strip()

    coupon = db.query(BloodTestCoupon).filter(
        func.upper(BloodTestCoupon.coupon_code) == normalized_code
    ).first()

    if not coupon:
        for c in db.query(BloodTestCoupon).all():
            if c.coupon_code and c.coupon_code.upper().strip() == normalized_code:
                coupon = c
                break

    if not coupon:
        return None, 0.0, "Invalid coupon code."

    # Check user allowlist
    from Login_module.User.user_model import User as _User
    _user = db.query(_User).filter(_User.id == user_id).first()
    _mobile = _user.mobile if _user else ""
    if not is_user_allowed_for_coupon(db, coupon, user_id, _mobile or ""):
        return None, 0.0, "Invalid coupon code."

    # Status check
    if coupon.status != BloodTestCouponStatus.ACTIVE:
        return None, 0.0, f"Coupon '{coupon.coupon_code}' is not active."

    # Validity period
    from Login_module.Utils.datetime_utils import to_ist
    now = now_ist()
    valid_from_ist = to_ist(coupon.valid_from) if coupon.valid_from else None
    valid_until_ist = to_ist(coupon.valid_until) if coupon.valid_until else None

    if valid_from_ist and now < valid_from_ist:
        return None, 0.0, f"Coupon '{coupon.coupon_code}' is not yet valid."

    if valid_until_ist and now > valid_until_ist:
        return None, 0.0, f"This coupon expired on {valid_until_ist.strftime('%d %b %Y')}. Check for other available offers."

    # Minimum order amount
    if subtotal_amount < coupon.min_order_amount:
        return None, 0.0, (
            f"Minimum amount of ₹{coupon.min_order_amount} needed to apply this coupon. "
            f"Your blood test cart total is ₹{subtotal_amount}."
        )

    # Global usage limit
    if coupon.max_uses is not None:
        confirmed_uses = (
            db.query(func.count(BloodTestCouponUsage.id))
            .filter(BloodTestCouponUsage.coupon_id == coupon.id)
            .scalar()
            or 0
        )
        if confirmed_uses >= coupon.max_uses:
            return None, 0.0, "Sorry, this coupon is no longer available."

    # Per-user limit
    max_per_user = coupon.max_uses_per_user if coupon.max_uses_per_user is not None else 1
    user_uses = (
        db.query(func.count(BloodTestCouponUsage.id))
        .filter(
            BloodTestCouponUsage.coupon_id == coupon.id,
            BloodTestCouponUsage.user_id == user_id,
        )
        .scalar()
        or 0
    )
    if user_uses >= max_per_user:
        return None, 0.0, "You have already used this coupon on a previous order."

    # Calculate discount
    discount_amount = 0.0
    if coupon.discount_type == BloodTestCouponType.PERCENTAGE:
        discount_amount = (subtotal_amount * coupon.discount_value) / 100.0
        if coupon.max_discount_amount is not None:
            discount_amount = min(discount_amount, coupon.max_discount_amount)
    elif coupon.discount_type == BloodTestCouponType.FIXED:
        discount_amount = min(coupon.discount_value, subtotal_amount)
        if coupon.max_discount_amount is not None:
            discount_amount = min(discount_amount, coupon.max_discount_amount)

    return coupon, discount_amount, ""


def apply_coupon_to_cart(
    db: Session,
    user_id: int,
    coupon_code: str,
    subtotal_amount: float,
) -> Tuple[bool, float, str, Optional[BloodTestCoupon]]:
    """Apply blood-test coupon to cart."""
    coupon, discount_amount, error_message = validate_and_calculate_discount(
        db, coupon_code, user_id, subtotal_amount
    )

    if not coupon:
        return False, 0.0, error_message or "Invalid coupon", None

    existing = db.query(BloodTestCartCoupon).filter(
        BloodTestCartCoupon.user_id == user_id
    ).first()

    if existing:
        existing.coupon_id = coupon.id
        existing.coupon_code = coupon.coupon_code
        existing.discount_amount = discount_amount
        existing.applied_at = now_ist()
        db.flush()
    else:
        cart_coupon = BloodTestCartCoupon(
            user_id=user_id,
            coupon_id=coupon.id,
            coupon_code=coupon.coupon_code,
            discount_amount=discount_amount,
            applied_at=now_ist(),
        )
        db.add(cart_coupon)
        db.flush()

    return True, discount_amount, f"Coupon '{coupon.coupon_code}' applied successfully", coupon


def get_applied_coupon(db: Session, user_id: int) -> Optional[BloodTestCartCoupon]:
    """Get the currently applied blood-test coupon for a user."""
    return (
        db.query(BloodTestCartCoupon)
        .filter(BloodTestCartCoupon.user_id == user_id)
        .order_by(BloodTestCartCoupon.applied_at.desc())
        .first()
    )


def remove_coupon_from_cart(db: Session, user_id: int) -> bool:
    """Remove applied blood-test coupon from user's cart."""
    cart_coupon = get_applied_coupon(db, user_id)
    if cart_coupon:
        db.delete(cart_coupon)
        db.flush()
        return True
    return False


def record_coupon_usage(
    db: Session,
    coupon_code: str,
    user_id: int,
    order_id: int,
    order_number: str,
    discount_amount: float,
) -> None:
    """Record a confirmed blood-test coupon usage. Idempotent."""
    if not coupon_code:
        return

    coupon = db.query(BloodTestCoupon).filter(
        func.upper(BloodTestCoupon.coupon_code) == coupon_code.upper().strip()
    ).first()

    if not coupon:
        logger.warning(f"record_coupon_usage: blood-test coupon '{coupon_code}' not found, skipping.")
        return

    existing = db.query(BloodTestCouponUsage).filter(
        BloodTestCouponUsage.coupon_id == coupon.id,
        BloodTestCouponUsage.order_id == order_id,
    ).first()

    if existing:
        return

    usage = BloodTestCouponUsage(
        coupon_id=coupon.id,
        coupon_code=coupon.coupon_code,
        user_id=user_id,
        order_id=order_id,
        order_number=order_number,
        discount_amount=discount_amount,
        used_at=now_ist(),
    )
    db.add(usage)
    db.flush()


def get_coupon_usage_count(db: Session, coupon_id: int) -> int:
    """Get confirmed usage count for a blood-test coupon."""
    return (
        db.query(func.count(BloodTestCouponUsage.id))
        .filter(BloodTestCouponUsage.coupon_id == coupon_id)
        .scalar()
        or 0
    )


def is_coupon_usage_limit_reached(db: Session, coupon: BloodTestCoupon) -> bool:
    """Check if a blood-test coupon has reached its max_uses limit."""
    if coupon.max_uses is None:
        return False
    return get_coupon_usage_count(db, coupon.id) >= coupon.max_uses
