"""
Coupon service for validating and applying coupons.
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from datetime import datetime
from typing import Optional, Tuple
import logging
from .Coupon_model import Coupon, CartCoupon, CouponUsage, CouponType, CouponStatus
from Login_module.Utils.datetime_utils import now_ist

logger = logging.getLogger(__name__)


def is_user_allowed_for_coupon(db: Session, coupon, user_id: int, mobile: str) -> bool:
    """
    Returns True if the coupon is unrestricted OR the user is on the allowlist.
    A coupon is unrestricted when it has zero coupon_allowed_users rows.
    """
    from .Coupon_model import CouponAllowedUser
    from sqlalchemy import or_
    count = db.query(func.count(CouponAllowedUser.id)).filter(
        CouponAllowedUser.coupon_id == coupon.id
    ).scalar() or 0
    if count == 0:
        return True  # unrestricted — no allowlist entries
    match = db.query(CouponAllowedUser).filter(
        CouponAllowedUser.coupon_id == coupon.id,
        or_(
            CouponAllowedUser.user_id == user_id,
            CouponAllowedUser.mobile == mobile
        )
    ).first()
    return match is not None


def validate_and_calculate_discount(
    db: Session,
    coupon_code: str,
    user_id: int,
    subtotal_amount: float,
    cart_items: Optional[list] = None
) -> Tuple[Optional[Coupon], float, str]:
    """
    Validate coupon and calculate discount amount.
    Usage count is based on confirmed orders (coupon_usages table).
    """
    if not coupon_code:
        return None, 0.0, ""

    normalized_code = coupon_code.upper().strip()
    logger.info(f"Validating coupon code: '{coupon_code}' (normalized: '{normalized_code}')")

    coupon = db.query(Coupon).filter(
        func.upper(Coupon.coupon_code) == normalized_code
    ).first()

    if not coupon:
        all_coupons = db.query(Coupon).all()
        for c in all_coupons:
            if c.coupon_code and c.coupon_code.upper().strip() == normalized_code:
                coupon = c
                break

    if not coupon:
        total_coupons = db.query(Coupon).count()
        logger.warning(f"Coupon '{normalized_code}' not found. Total coupons in database: {total_coupons}")
        return None, 0.0, "Invalid coupon code."

    logger.info(f"Found coupon: {coupon.coupon_code} (ID: {coupon.id}, Status: {coupon.status})")

    # Check user allowlist restriction
    from Login_module.User.user_model import User as _User
    _user = db.query(_User).filter(_User.id == user_id).first()
    _mobile = _user.mobile if _user else ""
    if not is_user_allowed_for_coupon(db, coupon, user_id, _mobile or ""):
        return None, 0.0, "Invalid coupon code."

    # Check active status
    if coupon.status != CouponStatus.ACTIVE:
        return None, 0.0, f"Coupon '{coupon.coupon_code}' is not active (status: {coupon.status.value})"

    # Check validity period
    from Login_module.Utils.datetime_utils import to_ist
    now = now_ist()
    valid_from_ist = to_ist(coupon.valid_from) if coupon.valid_from else None
    valid_until_ist = to_ist(coupon.valid_until) if coupon.valid_until else None

    if valid_from_ist and now < valid_from_ist:
        return None, 0.0, f"Coupon '{coupon.coupon_code}' is not yet valid. Valid from: {valid_from_ist.strftime('%Y-%m-%d')}"

    if valid_until_ist and now > valid_until_ist:
        return None, 0.0, f"This coupon expired on {valid_until_ist.strftime('%d %b %Y')}. Check for other available offers."

    # Check minimum order amount
    if subtotal_amount < coupon.min_order_amount:
        return None, 0.0, (
            f"Minimum amount of ₹{coupon.min_order_amount} needed to apply this coupon. "
            f"Your cart total is ₹{subtotal_amount}."
        )

    # Check total usage limit against confirmed orders (coupon_usages table)
    if coupon.max_uses is not None:
        confirmed_uses = db.query(func.count(CouponUsage.id)).filter(
            CouponUsage.coupon_id == coupon.id
        ).scalar() or 0

        if confirmed_uses >= coupon.max_uses:
            logger.warning(f"Coupon '{coupon.coupon_code}' usage limit reached. Used: {confirmed_uses}/{coupon.max_uses}")
            return None, 0.0, "Sorry, this coupon is no longer available."

    # Check per-user usage limit
    max_per_user = coupon.max_uses_per_user if coupon.max_uses_per_user is not None else 1
    user_uses = db.query(func.count(CouponUsage.id)).filter(
        CouponUsage.coupon_id == coupon.id,
        CouponUsage.user_id == user_id
    ).scalar() or 0

    if user_uses >= max_per_user:
        logger.warning(f"Coupon '{coupon.coupon_code}' per-user limit reached for user {user_id}. Used: {user_uses}/{max_per_user}")
        return None, 0.0, "You have already used this coupon on a previous order."

    # Check allowed plan types restriction
    if coupon.allowed_plan_types:
        allowed = [p.strip().lower() for p in coupon.allowed_plan_types.split(",") if p.strip()]
        if allowed:
            # Resolve cart items if not provided
            if cart_items is None:
                from .Cart_model import CartItem
                from sqlalchemy.orm import joinedload
                cart_items = db.query(CartItem).options(
                    joinedload(CartItem.product)
                ).filter(
                    CartItem.user_id == user_id,
                    CartItem.is_deleted == False
                ).all()

            if not cart_items:
                return None, 0.0, "Your cart is empty. Add eligible products to use this coupon."

            # Collect unique plan types in cart
            cart_plan_types = set()
            for item in cart_items:
                if item.product and item.product.plan_type:
                    pt = item.product.plan_type
                    if hasattr(pt, 'value'):
                        pt = pt.value
                    cart_plan_types.add(str(pt).lower())

            # Check if any cart item matches an allowed plan type
            if not cart_plan_types.intersection(allowed):
                readable = ", ".join(p.capitalize() for p in allowed)
                return None, 0.0, f"This coupon is only valid for {readable} plan(s). Please add an eligible product to your cart."

            logger.info(f"Plan type check passed for coupon '{normalized_code}': cart={cart_plan_types}, allowed={allowed}")

    # Legacy hardcoded check for FAMILYCOUPLE30
    if normalized_code == "FAMILYCOUPLE30":
        if cart_items is None:
            from .Cart_model import CartItem
            from sqlalchemy.orm import joinedload
            cart_items = db.query(CartItem).options(
                joinedload(CartItem.product)
            ).filter(
                CartItem.user_id == user_id,
                CartItem.is_deleted == False
            ).all()

        from collections import defaultdict
        grouped_items = defaultdict(list)
        for item in (cart_items or []):
            group_key = item.group_id or f"single_{item.id}"
            grouped_items[group_key].append(item)

        has_family_plan = False
        has_couple_plan = False
        for group_key, items in grouped_items.items():
            if items and items[0].product:
                pt = items[0].product.plan_type
                if hasattr(pt, 'value'):
                    pt = pt.value
                pt = str(pt).lower()
                if pt == "family":
                    has_family_plan = True
                elif pt == "couple":
                    has_couple_plan = True

        if not has_family_plan:
            return None, 0.0, "This coupon requires at least one Family plan in your cart."
        if not has_couple_plan:
            return None, 0.0, "This coupon requires at least one Couple plan in your cart."

    # Calculate discount
    discount_amount = 0.0
    if coupon.discount_type == CouponType.PERCENTAGE:
        discount_amount = (subtotal_amount * coupon.discount_value) / 100.0
        if coupon.max_discount_amount is not None:
            discount_amount = min(discount_amount, coupon.max_discount_amount)
    elif coupon.discount_type == CouponType.FIXED:
        discount_amount = min(coupon.discount_value, subtotal_amount)
        if coupon.max_discount_amount is not None:
            discount_amount = min(discount_amount, coupon.max_discount_amount)

    return coupon, discount_amount, ""


def record_coupon_usage(
    db: Session,
    coupon_code: str,
    user_id: int,
    order_id: int,
    order_number: str,
    discount_amount: float
) -> None:
    """
    Record a confirmed coupon usage in coupon_usages table.
    Called after order is confirmed via webhook. Idempotent — skips if already recorded.
    """
    if not coupon_code:
        return

    coupon = db.query(Coupon).filter(
        func.upper(Coupon.coupon_code) == coupon_code.upper().strip()
    ).first()

    if not coupon:
        logger.warning(f"record_coupon_usage: coupon '{coupon_code}' not found, skipping.")
        return

    # Idempotency: don't double-record for the same order
    existing = db.query(CouponUsage).filter(
        CouponUsage.coupon_id == coupon.id,
        CouponUsage.order_id == order_id
    ).first()

    if existing:
        logger.info(f"Coupon usage already recorded for order {order_number}, skipping.")
        return

    usage = CouponUsage(
        coupon_id=coupon.id,
        coupon_code=coupon.coupon_code,
        user_id=user_id,
        order_id=order_id,
        order_number=order_number,
        discount_amount=discount_amount,
        used_at=now_ist()
    )
    db.add(usage)
    db.flush()
    logger.info(f"Recorded coupon usage: '{coupon_code}' for order {order_number} (user {user_id})")


def apply_coupon_to_cart(
    db: Session,
    user_id: int,
    coupon_code: str,
    subtotal_amount: float,
    cart_items: Optional[list] = None
) -> Tuple[bool, float, str, Optional[Coupon]]:
    """Apply coupon to cart and record the application."""
    coupon, discount_amount, error_message = validate_and_calculate_discount(
        db, coupon_code, user_id, subtotal_amount, cart_items
    )

    if not coupon:
        return False, 0.0, error_message or "Invalid coupon", None

    existing_cart_coupon = db.query(CartCoupon).filter(
        CartCoupon.user_id == user_id
    ).first()

    if existing_cart_coupon:
        existing_cart_coupon.coupon_id = coupon.id
        existing_cart_coupon.coupon_code = coupon.coupon_code
        existing_cart_coupon.discount_amount = discount_amount
        existing_cart_coupon.applied_at = now_ist()
        db.flush()
        logger.info(f"Updated existing CartCoupon record for user {user_id}")
    else:
        cart_coupon = CartCoupon(
            user_id=user_id,
            coupon_id=coupon.id,
            coupon_code=coupon.coupon_code,
            discount_amount=discount_amount,
            applied_at=now_ist(),
        )
        db.add(cart_coupon)
        db.flush()
        logger.info(f"Created new CartCoupon record for user {user_id}")

    return True, discount_amount, f"Coupon '{coupon.coupon_code}' applied successfully", coupon


def get_applied_coupon(db: Session, user_id: int) -> Optional[CartCoupon]:
    """Get the most recently applied coupon for a user."""
    return db.query(CartCoupon).filter(
        CartCoupon.user_id == user_id
    ).order_by(CartCoupon.applied_at.desc()).first()


def remove_coupon_from_cart(db: Session, user_id: int) -> bool:
    """Remove applied coupon from user's cart. Uses flush to stay within caller's transaction."""
    cart_coupon = get_applied_coupon(db, user_id)
    if cart_coupon:
        db.delete(cart_coupon)
        db.flush()
        return True
    return False


def get_coupon_usage_count(db: Session, coupon_id: int) -> int:
    """Get confirmed usage count from coupon_usages table."""
    return db.query(func.count(CouponUsage.id)).filter(
        CouponUsage.coupon_id == coupon_id
    ).scalar() or 0


def is_coupon_usage_limit_reached(db: Session, coupon: Coupon) -> bool:
    """Check if a coupon has reached its max_uses limit."""
    if coupon.max_uses is None:
        return False
    return get_coupon_usage_count(db, coupon.id) >= coupon.max_uses
