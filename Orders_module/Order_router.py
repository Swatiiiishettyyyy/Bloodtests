"""
Order router - handles order creation, payment, and tracking.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional, Dict, Callable
from collections import defaultdict
import uuid
import logging
import json

from deps import get_db
from Login_module.Utils.auth_user import get_current_user, get_current_member
from Login_module.Utils.datetime_utils import now_ist, to_ist_isoformat
from Login_module.User.user_model import User
from Member_module.Member_model import Member
from .Order_schema import (
    CreateOrderRequest,
    OrderResponse,
    RazorpayOrderResponse,
    VerifyPaymentRequest,
    PaymentVerificationResponse,
    UpdateOrderStatusRequest,
    OrderItemTrackingResponse,
    OrderTrackingResponse,
    RazorpayWebhookPayload,
    WebhookResponse,
    ScheduleOrderRequest,
)
from .Order_crud import (
    create_order_from_cart,
    verify_payment_frontend,
    confirm_order_from_webhook,
    update_order_status,
    get_order_by_id,
    get_order_by_number,
    get_user_orders,
    mark_payment_failed_or_cancelled
)
from .razorpay_service import create_razorpay_order, verify_webhook_signature, get_payment_details, create_razorpay_invoice_for_order, razorpay_client, RAZORPAY_KEY_ID
from .Order_model import OrderStatus, PaymentStatus, PaymentMethod, Order, OrderItem, Payment, PaymentTransition, WebhookLog
from Cart_module.Cart_model import CartItem, Cart
from Notification_module.Notification_crud import send_notification_to_user
from config import settings
import razorpay

# Admin password for PUT order status endpoint — set ORDER_STATUS_PASSWORD env var in production
ORDER_STATUS_PASSWORD = settings.ORDER_STATUS_PASSWORD

router = APIRouter(prefix="/orders", tags=["Orders"])


def verify_order_status_password(
    x_order_status_password: Optional[str] = Header(None, alias="X-Order-Status-Password")
) -> None:
    """Require password 4567 for PUT order status endpoint."""
    if not x_order_status_password or x_order_status_password != ORDER_STATUS_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing order status password. Send correct password in header: X-Order-Status-Password."
        )

logger = logging.getLogger(__name__)


def get_client_info(request: Request):
    """Extract client IP and user agent from request"""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return ip, user_agent


def extract_and_validate_group_id(snapshot) -> Optional[str]:
    """
    Extract and validate group_id from order snapshot's cart_item_data.
    
    This function:
    - Extracts group_id from JSON data stored in snapshot
    - Validates that group_id is a valid string (not None, not empty, not wrong type)
    - Returns None if group_id is invalid or missing (for backward compatibility)
    
    Args:
        snapshot: OrderSnapshot object containing cart_item_data JSON
        
    Returns:
        str: Valid group_id if found and valid, None otherwise
    """
    if not snapshot or not snapshot.cart_item_data:
        return None
    
    group_id = snapshot.cart_item_data.get("group_id")
    
    # Validate group_id: must be a non-empty string
    if group_id is None:
        return None
    
    # Ensure it's a string type (handle edge cases where JSON might have wrong type)
    if not isinstance(group_id, str):
        logger.warning(f"Invalid group_id type: {type(group_id)}, expected str. Value: {group_id}")
        return None
    
    # Ensure it's not empty
    if not group_id.strip():
        logger.warning(f"Empty group_id found in snapshot {snapshot.id}")
        return None
    
    return group_id


def _thyrocare_line_extras_factory(db: Session, our_order_id: int, items: List[OrderItem]) -> Callable[[OrderItem], Dict]:
    """
    Build a per-line map for Thyrocare bookings on a nucleotide order.
    Multiple products / visits => multiple thyrocare_order_id values; ref_order_no comes from thyrocare_order_tracking.
    """
    from Thyrocare_module.thyrocare_webhook_model import ThyrocareOrderTracking
    from Thyrocare_module.Thyrocare_model import ThyrocareProduct

    trackings = (
        db.query(ThyrocareOrderTracking)
        .filter(ThyrocareOrderTracking.our_order_id == our_order_id)
        .all()
    )
    ref_by_tc_id: Dict[str, Optional[str]] = {}
    for t in trackings:
        if t.thyrocare_order_id:
            ref_by_tc_id[str(t.thyrocare_order_id).strip()] = t.ref_order_no

    tc_pids = {i.thyrocare_product_id for i in items if i.thyrocare_product_id}
    catalog_id_by_row: Dict[int, str] = {}
    if tc_pids:
        for p in db.query(ThyrocareProduct).filter(ThyrocareProduct.id.in_(tc_pids)).all():
            catalog_id_by_row[p.id] = p.thyrocare_id

    def extras(item: OrderItem) -> Dict:
        tid = item.thyrocare_order_id
        tid_s = str(tid).strip() if tid else None
        ref = ref_by_tc_id.get(tid_s) if tid_s else None
        pid = item.thyrocare_product_id
        return {
            "thyrocare_order_id": tid_s,
            "thyrocare_ref_order_no": ref,
            "thyrocare_booking_status": item.thyrocare_booking_status,
            "thyrocare_product_id": pid,
            "thyrocare_catalog_product_id": catalog_id_by_row.get(pid) if pid else None,
        }

    return extras


@router.post("/create", response_model=RazorpayOrderResponse)
def create_order(
    request_data: CreateOrderRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    current_member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """
    Create order from all cart items for the authenticated user.
    Creates Razorpay order for payment.
    No COD option - payment must be completed online.
    
    Request: {"cart_id": 1} - cart_id is used as reference, all authenticated user's cart items are included
    Response: {"order_id": 1, "order_number": "ORD123456", "razorpay_order_id": "...", "amount": 27550, "currency": "INR"}
    
    Note: user_id is automatically fetched from the access token (current_user)
    """
    try:
        # Get user's active cart first
        cart = db.query(Cart).filter(
            Cart.user_id == current_user.id,
            Cart.is_active == True
        ).first()
        
        # Get all cart items for the user (exclude deleted items)
        # First try via cart_id if cart exists, otherwise fallback to user_id
        if cart:
            cart_items = db.query(CartItem).filter(
                CartItem.cart_id == cart.id,
                CartItem.is_deleted == False  # Exclude deleted/cleared items
            ).order_by(CartItem.group_id, CartItem.created_at).all()
        else:
            # Fallback for backward compatibility - query by user_id
            cart_items = db.query(CartItem).filter(
                CartItem.user_id == current_user.id,
                CartItem.is_deleted == False  # Exclude deleted/cleared items
            ).order_by(CartItem.group_id, CartItem.created_at).all()
            
            # If cart doesn't exist but user has items, create cart
            if cart_items:
                # Get all active carts for this user
                active_carts = db.query(Cart).filter(
                    Cart.user_id == current_user.id,
                    Cart.is_active == True
                ).all()
                
                if len(active_carts) > 1:
                    # Multiple active carts - deactivate all except the first
                    for c in active_carts[1:]:
                        c.is_active = False
                    cart = active_carts[0]
                elif len(active_carts) == 1:
                    cart = active_carts[0]
                else:
                    # No active cart exists - create new one
                    cart = Cart(
                        user_id=current_user.id,
                        is_active=True
                    )
                    db.add(cart)
                    db.flush()
                    db.refresh(cart)
                
                # Update items to use the new cart_id
                for item in cart_items:
                    if not item.cart_id:
                        item.cart_id = cart.id
                db.flush()  # Flush - will be committed with order creation
        
        # Validate that cart has items
        if not cart_items:
            client_ip = get_client_info(request) if request else None
            logger.warning(
                f"Order creation failed - Empty cart | "
                f"User ID: {current_user.id} | Cart ID: {request_data.cart_id} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your cart is empty. Please add items to your cart before placing an order."
            )

        # Blood tests (Thyrocare) require slot selection per group before checkout.
        # Enforce this upfront so OrderSnapshot always contains appointment fields and booking
        # never has to guess via cart fallback.
        blood_groups_missing_slot = {}
        for item in cart_items:
            if getattr(item, "product_type", None) == "blood_test":
                gk = item.group_id or f"single_{item.id}"
                if not item.appointment_date or not item.appointment_start_time:
                    blood_groups_missing_slot.setdefault(gk, []).append(item.id)

        if blood_groups_missing_slot:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "Appointment date and time must be set for each blood test before checkout.",
                    "blood_test_groups_missing_slot": blood_groups_missing_slot,
                },
            )
        
        # Validate that the provided cart_id matches the user's active cart
        if cart and request_data.cart_id != cart.id:
            logger.warning(
                f"Cart ID {request_data.cart_id} does not match active cart {cart.id} for user {current_user.id}. "
                f"Proceeding with active cart items ({len(cart_items)} items)."
            )
        
        # Get unique addresses from cart items
        unique_cart_address_ids = {item.address_id for item in cart_items if item.address_id}
        
        if not unique_cart_address_ids:
            client_ip = get_client_info(request) if request else None
            logger.warning(
                f"Order creation failed - Cart items missing valid addresses | "
                f"User ID: {current_user.id} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Your cart items need valid addresses. Please check your addresses and try again."
            )
        
        # Use the first address from cart items as primary address
        primary_address_id = next(iter(sorted(unique_cart_address_ids)))
        
        # Verify the address belongs to the user
        from Address_module.Address_model import Address
        address = db.query(Address).filter(
            Address.id == primary_address_id,
            Address.user_id == current_user.id,
            Address.is_deleted == False
        ).first()
        
        if not address:
            client_ip = get_client_info(request) if request else None
            logger.warning(
                f"Order creation failed - Address not found or unauthorized | "
                f"User ID: {current_user.id} | Address ID: {primary_address_id} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="We couldn't find this address, or it doesn't belong to your account."
            )
        
        # Calculate total amount (will be recalculated in create_order_from_cart with coupon)
        # This is just for Razorpay order creation - actual order will have correct totals
        subtotal = 0.0
        delivery_charge = 0.0
        grouped_items = {}
        
        for item in cart_items:
            group_key = item.group_id or f"single_{item.id}"
            if group_key not in grouped_items:
                grouped_items[group_key] = []
            grouped_items[group_key].append(item)

        for group_key, items in grouped_items.items():
            # Skip if group is empty (should not happen, but safety check)
            if not items:
                continue
                
            item = items[0]

            # Blood test items: do NOT query Thyrocare cart/price-breakup for net payable.
            # Use our catalog selling_price per member instead.
            if item.product_type == "blood_test" and item.thyrocare_product:
                unit_price = float(item.thyrocare_product.selling_price or 0)
                subtotal += unit_price * len(items)
                continue

            # Skip if genetic product is deleted or missing
            product = item.product
            if not product:
                continue
                
            subtotal += item.quantity * product.SpecialPrice
        
        # Get coupon discount for Razorpay order amount
        # Coupons apply to genetic test items only — exclude blood test subtotal
        from Cart_module.coupon_service import get_applied_coupon, validate_and_calculate_discount
        from Cart_module.Cart_model import ProductType as _PT
        genetic_subtotal = sum(
            item.quantity * item.product.SpecialPrice
            for item in cart_items
            if getattr(item, 'product_type', None) != _PT.BLOOD_TEST and item.product
        )
        applied_coupon = get_applied_coupon(db, current_user.id)
        coupon_discount = 0.0

        if applied_coupon:
            coupon, recalculated_discount, error_message = validate_and_calculate_discount(
                db,
                applied_coupon.coupon_code,
                current_user.id,
                genetic_subtotal,
                cart_items,
            )
            if coupon and not error_message:
                coupon_discount = recalculated_discount
            else:
                coupon_discount = 0.0
        
        # Calculate product discount (per product group, not per cart item row)
        processed_groups = set()
        product_discount = 0.0
        for item in cart_items:
            group_key = item.group_id or f"single_{item.id}"

            # Blood test discount: listing_price - selling_price per member in group
            if item.product_type == "blood_test" and item.thyrocare_product:
                if group_key not in processed_groups:
                    group_items = [ci for ci in cart_items if (ci.group_id or f"single_{ci.id}") == group_key]
                    discount_per_member = max(0, item.thyrocare_product.listing_price - item.thyrocare_product.selling_price)
                    product_discount += discount_per_member * len(group_items)
                    processed_groups.add(group_key)
                continue

            # Skip if genetic product is deleted or missing
            if not item.product:
                continue

            if group_key not in processed_groups:
                discount_per_item = item.product.Price - item.product.SpecialPrice
                product_discount += discount_per_item * item.quantity
                processed_groups.add(group_key)
        
        # Calculate total amount
        # Note: subtotal already uses SpecialPrice (product discount is already applied)
        # So we only subtract coupon_discount, not product_discount
        # This matches the corrected cart calculation: grand_total = subtotal_amount + delivery_charge - coupon_amount
        total_amount = subtotal + delivery_charge - coupon_discount
        total_amount = max(0.0, total_amount)  # Ensure not negative

        # Get all cart item IDs
        cart_item_ids = [item.id for item in cart_items]
        
        # Check for existing order for retry payment scenario
        from .Order_crud import find_existing_order_for_retry
        existing_order = find_existing_order_for_retry(
            db=db,
            user_id=current_user.id,
            cart_item_ids=cart_item_ids
        )
        
        if existing_order:
            # Reuse existing order - update razorpay_order_id and reset statuses
            # Don't create new order, just update the existing one
            
            # Capture previous status before updating
            previous_order_status = existing_order.order_status
            previous_payment_status = existing_order.payment_status
            
            # Create new Razorpay order
            razorpay_order = create_razorpay_order(
                amount=total_amount,
                currency="INR",
                receipt=f"order_{current_user.id}_{uuid.uuid4().hex[:8]}",
                notes={
                    "user_id": str(current_user.id),
                    "address_id": str(primary_address_id) if primary_address_id else "None",
                    "order_number": existing_order.order_number,
                    "retry": "true"
                }
            )
            
            # Update existing order
            existing_order.order_status = OrderStatus.PENDING_PAYMENT
            existing_order.payment_status = PaymentStatus.PENDING  # Denormalized
            existing_order.status_updated_at = now_ist()
            existing_order.updated_at = now_ist()
            # Sync coupon and totals in case coupon was applied/changed since first attempt
            existing_order.coupon_code = applied_coupon.coupon_code if applied_coupon and coupon_discount > 0 else None
            existing_order.coupon_discount = coupon_discount
            existing_order.total_amount = total_amount
            
            # Create new payment record for retry
            from .Order_model import Payment, PaymentTransition
            new_payment = Payment(
                order_id=existing_order.id,
                payment_method=PaymentMethod.RAZORPAY,
                payment_status=PaymentStatus.PENDING,
                razorpay_order_id=razorpay_order.get("id"),
                amount=total_amount,
                currency="INR",
                notes=f"Payment retry for order {existing_order.order_number}. Previous status: {previous_payment_status.value}"
            )
            db.add(new_payment)
            db.flush()
            
            # Create payment transition
            payment_transition = PaymentTransition(
                payment_id=new_payment.id,
                from_status=None,  # New payment attempt
                to_status=PaymentStatus.PENDING,
                transition_reason=f"Payment retry initiated. Previous payment status: {previous_payment_status.value}",
                triggered_by="system"
            )
            db.add(payment_transition)
            
            # Update order items status
            item_previous_statuses = {}
            for item in existing_order.items:
                item_previous_statuses[item.id] = item.order_status
                item.order_status = OrderStatus.PENDING_PAYMENT
                item.status_updated_at = now_ist()
            
            # Create status history for retry
            from .Order_model import OrderStatusHistory
            status_history = OrderStatusHistory(
                order_id=existing_order.id,
                status=OrderStatus.PENDING_PAYMENT,
                previous_status=previous_order_status,
                notes=f"Order retry - new Razorpay order created. Previous status: {previous_order_status.value}, Previous payment status: {previous_payment_status.value}. Payment retry initiated.",
                changed_by="system"
            )
            db.add(status_history)
            
            # Create status history for each order item
            for item in existing_order.items:
                item_previous_status = item_previous_statuses.get(item.id, OrderStatus.PENDING_PAYMENT)
                item_status_history = OrderStatusHistory(
                    order_id=existing_order.id,
                    order_item_id=item.id,
                    status=OrderStatus.PENDING_PAYMENT,
                    previous_status=item_previous_status,
                    notes="Order item retry - payment retry initiated.",
                    changed_by="system"
                )
                db.add(item_status_history)
            
            db.commit()
            db.refresh(existing_order)
            
            logger.info(f"Order {existing_order.order_number} updated for retry payment (user {current_user.id})")
            order = existing_order
        else:
            # No pending checkout yet — create Razorpay order first, then PendingCheckout.
            # The real Order row (with order number) is created ONLY after payment is confirmed.
            razorpay_order = create_razorpay_order(
                amount=total_amount,
                currency="INR",
                receipt=f"pc_{current_user.id}_{uuid.uuid4().hex[:8]}",
                notes={
                    "user_id": str(current_user.id),
                    "address_id": str(primary_address_id) if primary_address_id else "None"
                }
            )

            from .Order_crud import create_pending_checkout
            from .pending_checkout_model import PendingCheckout
            placed_by_member_id = current_member.id if current_member else None
            pending = create_pending_checkout(
                db=db,
                user_id=current_user.id,
                razorpay_order_id=razorpay_order.get("id"),
                amount=total_amount,
                cart_item_ids=cart_item_ids,
                address_id=primary_address_id,
                placed_by_member_id=placed_by_member_id,
                coupon_code=applied_coupon.coupon_code if applied_coupon and coupon_discount > 0 else None,
                coupon_discount=coupon_discount,
            )
            logger.info(
                f"PendingCheckout {pending.id} created for user {current_user.id} "
                f"(Razorpay order {razorpay_order.get('id')})"
            )

        # pending_checkout_id returned as order_id so the frontend contract is unchanged.
        # The real order_id / order_number are returned by verify-payment after payment.
        if existing_order:
            # Retry path: existing_order is a real Order row — return its data
            return RazorpayOrderResponse(
                order_id=existing_order.id,
                order_number=existing_order.order_number,
                razorpay_order_id=razorpay_order.get("id"),
                amount=total_amount,
                currency="INR",
                pending_checkout_id=None,  # legacy retry — no PendingCheckout
            )
        return RazorpayOrderResponse(
            order_id=pending.id,          # frontend sends this back as order_id in verify-payment
            order_number=None,            # not yet — allocated on payment confirmation
            razorpay_order_id=razorpay_order.get("id"),
            amount=total_amount,
            currency="INR",
            pending_checkout_id=pending.id,
        )
    
    except HTTPException:
        # Re-raise HTTPException to preserve original status code (404, 400, etc.)
        db.rollback()
        raise
    except RuntimeError as e:
        db.rollback()
        client_ip = get_client_info(request) if request else None
        logger.error(
            f"Order creation failed - External service error | "
            f"User ID: {current_user.id} | Error: {str(e)} | IP: {client_ip}"
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except ConnectionError as e:
        db.rollback()
        client_ip = get_client_info(request) if request else None
        logger.error(
            f"Order creation failed - Payment gateway unreachable | "
            f"User ID: {current_user.id} | Error: {str(e)} | IP: {client_ip}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )
    except ValueError as e:
        db.rollback()
        client_ip = get_client_info(request) if request else None
        logger.warning(
            f"Order creation failed - Validation error | "
            f"User ID: {current_user.id} | Error: {str(e)} | IP: {client_ip}"
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        db.rollback()
        client_ip = get_client_info(request) if request else None
        logger.error(
            f"Order creation failed - Unexpected error | "
            f"User ID: {current_user.id} | Error: {type(e).__name__}: {str(e)} | IP: {client_ip}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {type(e).__name__}: {str(e)}"
        )


@router.post("/verify-payment", response_model=PaymentVerificationResponse)
def verify_payment(
    payment_data: VerifyPaymentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verify Razorpay payment from frontend.
    Provides immediate feedback but does NOT confirm order.
    Sets payment_status = PROCESSING and order_status = PROCESSING (frontend verified, waiting for webhook).
    Order confirmation happens via webhook only.
    Cart clearing happens via webhook only.
    """
    try:
        client_ip = get_client_info(request) if request else None
        from .pending_checkout_model import PendingCheckout
        from .Order_crud import create_order_post_payment

        # --- NEW PATH: order_id refers to a PendingCheckout ---
        pending = db.query(PendingCheckout).filter(
            PendingCheckout.id == payment_data.order_id,
            PendingCheckout.user_id == current_user.id,
        ).first()

        if pending:
            # Validate razorpay_order_id matches the pending checkout
            if pending.razorpay_order_id != payment_data.razorpay_order_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="razorpay_order_id does not match the checkout session."
                )

            # Idempotent: webhook already created the order
            if pending.order_id:
                order = db.query(Order).filter(Order.id == pending.order_id).first()
                return PaymentVerificationResponse(
                    status="success",
                    message="Payment verified successfully. Order confirmed.",
                    order_id=order.id,
                    order_number=order.order_number,
                    payment_status=order.payment_status.value,
                    order_status=order.order_status.value,
                )

            # Create the real Order now — order number allocated here for the first time
            order = create_order_post_payment(
                db=db,
                pending_checkout_id=pending.id,
                razorpay_payment_id=payment_data.razorpay_payment_id,
                razorpay_order_id=payment_data.razorpay_order_id,
            )
            logger.info(
                f"Order {order.order_number} created post-payment for user {current_user.id} "
                f"(PendingCheckout {pending.id})"
            )
            return PaymentVerificationResponse(
                status="success",
                message="Payment verified successfully. Order confirmed.",
                order_id=order.id,
                order_number=order.order_number,
                payment_status=order.payment_status.value,
                order_status=order.order_status.value,
            )

        # --- LEGACY PATH: order_id refers to an existing Order row (retry flow) ---
        order = db.query(Order).filter(Order.id == payment_data.order_id).first()
        if not order:
            logger.warning(
                f"Payment verification failed - neither PendingCheckout nor Order found | "
                f"ID: {payment_data.order_id} | User ID: {current_user.id} | IP: {client_ip}"
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checkout session not found.")

        if order.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Order does not belong to you.")

        if order.order_status == OrderStatus.CONFIRMED:
            return PaymentVerificationResponse(
                status="success",
                message="Payment verified successfully. Order confirmed.",
                order_id=order.id,
                order_number=order.order_number,
                payment_status=order.payment_status.value if hasattr(order.payment_status, 'value') else str(order.payment_status),
                order_status=order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
            )

        from .Order_crud import verify_payment_frontend
        order = verify_payment_frontend(
            db=db,
            order_id=payment_data.order_id,
            razorpay_order_id=payment_data.razorpay_order_id,
            razorpay_payment_id=payment_data.razorpay_payment_id,
            razorpay_signature=payment_data.razorpay_signature,
        )
        db.refresh(order)
        message = (
            "Payment verified successfully. Order confirmed."
            if order.order_status == OrderStatus.CONFIRMED
            else "Payment verified successfully. Waiting for confirmation..."
        )
        logger.info(f"Legacy payment verification for order {order.order_number} (user {current_user.id})")
        return PaymentVerificationResponse(
            status="success",
            message=message,
            order_id=order.id,
            order_number=order.order_number,
            payment_status=order.payment_status.value,
            order_status=order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
        )
    
    except ValueError as e:
        db.rollback()
        # Get the order to return payment status in error response
        payment_status = "unknown"
        order_status = None
        order_number = None
        
        try:
            order = db.query(Order).filter(Order.id == payment_data.order_id).first()
            if order:
                payment_status = order.payment_status.value if hasattr(order.payment_status, 'value') else str(order.payment_status)
                order_status = order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status)
                order_number = order.order_number
        except Exception as db_error:
            # Log database error but don't fail - use default values
            logger.warning(f"Error fetching order details for error response: {db_error}")
        
        # Return user-friendly error message with payment status
        error_message = str(e)
        payment_failed_indicator = "PAYMENT FAILED"
        
        if "Invalid payment signature" in error_message or "verification failed" in error_message.lower():
            error_message = f"{payment_failed_indicator}: Payment verification failed. Please check your payment details and try again. If the problem persists, please contact support."
            # Ensure payment_status is set to failed if not already
            if payment_status not in ["failed", "FAILED"]:
                payment_status = "failed"
        elif "already failed" in error_message.lower():
            error_message = f"{payment_failed_indicator}: Payment for this order has already failed. Please create a new order to retry payment."
            payment_status = "failed"
        elif "already cancelled" in error_message.lower():
            error_message = f"PAYMENT CANCELLED: Payment for this order was cancelled. Please create a new order to proceed with payment."
            payment_status = "cancelled"
        elif "already completed" in error_message.lower() or "already verified" in error_message.lower():
            error_message = "Payment for this order has already been verified successfully."
        else:
            # For any other payment-related error, mark as failed
            if payment_status == "unknown" or payment_status not in ["verified", "VERIFIED"]:
                error_message = f"{payment_failed_indicator}: {error_message}"
                payment_status = "failed"
        
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "failed",
                "error": error_message,
                "payment_status": payment_status.upper() if payment_status != "unknown" else "FAILED",
                "order_id": payment_data.order_id,
                "order_number": order_number,
                "order_status": order_status,
                "message": f"Payment verification failed. Payment status: {payment_status.upper() if payment_status != 'unknown' else 'FAILED'}"
            }
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Error verifying payment: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error verifying payment: {str(e)}"
        )


@router.post("/webhook", response_model=WebhookResponse)
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Razorpay webhook endpoint - final source of truth for payment verification.
    Handles payment.captured, payment.failed, and order.paid events.
    Only this endpoint can confirm orders (order_status = CONFIRMED).
    Only this endpoint clears carts (idempotent).
    
    Security:
    - Verifies webhook signature
    - Validates event authenticity
    - Handles race conditions with database locking
    """
    try:
        # Get raw request body for signature verification
        body_bytes = await request.body()
        body_str = body_bytes.decode('utf-8')
        
        # Get webhook signature from headers
        webhook_signature = request.headers.get("X-Razorpay-Signature")
        if not webhook_signature:
            logger.error("Missing X-Razorpay-Signature header in webhook request")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing webhook signature"
            )
        
        # Parse webhook payload first (before signature verification for logging)
        try:
            webhook_data = json.loads(body_str)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in webhook payload: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid webhook payload"
            )
        
        # Extract event type and payload
        event_type = webhook_data.get("event")
        event_id = webhook_data.get("id")  # Razorpay event ID
        event_payload = webhook_data.get("payload", {}).get("payment", {})
        entity = webhook_data.get("payload", {}).get("payment", {}).get("entity", {})
        
        if not event_type:
            logger.error("Missing event type in webhook payload")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing event type"
            )
        
        # Verify webhook signature
        from .razorpay_service import verify_webhook_signature
        is_valid = verify_webhook_signature(body_str, webhook_signature)
        
        # Log webhook event (before processing)
        webhook_log = WebhookLog(
            event_type=event_type,
            event_id=event_id,
            payload=webhook_data,
            signature_valid=is_valid,
            signature_verification_error=None if is_valid else "Invalid webhook signature",
            processed=False
        )
        db.add(webhook_log)
        db.flush()  # Get webhook_log.id
        
        if not is_valid:
            webhook_log.processed = True
            webhook_log.processing_error = "Invalid webhook signature"
            webhook_log.processed_at = now_ist()
            db.commit()
            logger.error("Invalid webhook signature")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )
        
        logger.info(f"Received webhook event: {event_type} (event_id: {event_id})")
        
        # Handle different event types
        if event_type == "payment.captured":
            # Extract payment details
            razorpay_payment_id = entity.get("id") or event_payload.get("id")
            razorpay_order_id = entity.get("order_id") or event_payload.get("order_id")
            
            if not razorpay_payment_id or not razorpay_order_id:
                logger.error(f"Missing payment_id or order_id in payment.captured webhook: {webhook_data}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing payment_id or order_id in webhook payload"
                )
            
            # Extract payment method details from Razorpay payload
            from .Order_crud import extract_payment_method_from_razorpay_payload
            payment_method_details, payment_method_metadata = extract_payment_method_from_razorpay_payload(entity)
            
            # --- NEW PATH: look for a PendingCheckout (deferred order creation) ---
            from .pending_checkout_model import PendingCheckout
            from .Order_crud import create_order_post_payment

            pending = db.query(PendingCheckout).filter(
                PendingCheckout.razorpay_order_id == razorpay_order_id
            ).first()

            if pending:
                webhook_log.processed = True
                try:
                    order = create_order_post_payment(
                        db=db,
                        pending_checkout_id=pending.id,
                        razorpay_payment_id=razorpay_payment_id,
                        razorpay_order_id=razorpay_order_id,
                        webhook_log_id=webhook_log.id,
                        payment_method_details=payment_method_details,
                        payment_method_metadata=payment_method_metadata,
                    )
                    webhook_log.order_id = order.id
                    webhook_log.processed_at = now_ist()
                    db.commit()
                    logger.info(
                        f"Webhook created Order {order.order_number} from PendingCheckout {pending.id}"
                    )
                except Exception as exc:
                    db.rollback()
                    webhook_log.processing_error = str(exc)
                    webhook_log.processed_at = now_ist()
                    db.commit()
                    logger.error(f"Webhook failed to create order from PendingCheckout {pending.id}: {exc}")
                return WebhookResponse(status="success", message="Order created and confirmed.")

            # --- LEGACY PATH: PendingCheckout not found, look for an existing Payment row ---
            payment = db.query(Payment).filter(
                Payment.razorpay_order_id == razorpay_order_id
            ).order_by(Payment.created_at.desc()).first()

            if not payment:
                logger.warning(f"Payment not found for Razorpay order ID: {razorpay_order_id}")
                webhook_log.processed = True
                webhook_log.processing_error = f"Payment not found for Razorpay order ID: {razorpay_order_id}"
                webhook_log.processed_at = now_ist()
                db.commit()
                return WebhookResponse(
                    status="success",
                    message=f"Payment not found for Razorpay order ID: {razorpay_order_id}"
                )

            # Update webhook log with payment info
            webhook_log.payment_id = payment.id
            webhook_log.order_id = payment.order_id
            webhook_log.razorpay_order_id = razorpay_order_id
            webhook_log.razorpay_payment_id = razorpay_payment_id
            
            # Get order
            order = db.query(Order).filter(Order.id == payment.order_id).first()
            
            if not order:
                logger.warning(f"Order not found for payment ID: {payment.id}")
                webhook_log.processed = True
                webhook_log.processing_error = f"Order not found for payment ID: {payment.id}"
                webhook_log.processed_at = now_ist()
                db.commit()
                return WebhookResponse(
                    status="success",
                    message=f"Order not found for payment ID: {payment.id}"
                )
            
            # Confirm order from webhook (idempotent)
            # Use savepoint to ensure we can rollback status changes on error
            savepoint = db.begin_nested()
            try:
                order = confirm_order_from_webhook(
                    db=db,
                    order_id=order.id,
                    razorpay_order_id=razorpay_order_id,
                    razorpay_payment_id=razorpay_payment_id,
                    webhook_log_id=webhook_log.id,
                    payment_method_details=payment_method_details,
                    payment_method_metadata=payment_method_metadata
                )
                
                # Commit the savepoint (status changes are now permanent)
                savepoint.commit()

                # Book Thyrocare order for any blood test items (non-blocking)
                try:
                    from Thyrocare_module.thyrocare_booking_service import book_thyrocare_for_order
                    book_thyrocare_for_order(db, order)
                except Exception as thyrocare_err:
                    logger.error(f"Thyrocare booking failed for order {order.order_number}: {thyrocare_err}")
                    # Do NOT fail the webhook — payment is confirmed, Thyrocare booking can be retried
                
                # Update webhook log as processed
                webhook_log.processed = True
                webhook_log.processed_at = now_ist()
                db.commit()
                
                logger.info(f"Order {order.order_number} confirmed by webhook (payment.captured)")
                
                return WebhookResponse(
                    status="success",
                    message=f"Order {order.order_number} confirmed successfully"
                )
                
            except ValueError as e:
                # Order already confirmed or other validation error
                # Rollback savepoint to undo any partial changes
                savepoint.rollback()
                logger.info(f"Webhook confirmation skipped: {str(e)}")
                # Update webhook log as processed (idempotent)
                webhook_log.processed = True
                webhook_log.processing_error = str(e)
                webhook_log.processed_at = now_ist()
                db.commit()
                # Return 200 OK (idempotent - already processed)
                return WebhookResponse(
                    status="success",
                    message=str(e)
                )
            except Exception as e:
                # Internal error - rollback savepoint to undo status changes
                savepoint.rollback()
                # Update webhook log with error (but no status changes were committed)
                webhook_log.processed = False
                webhook_log.processing_error = str(e)
                webhook_log.processed_at = now_ist()
                db.commit()
                logger.error(f"Error confirming order from webhook: {e}", exc_info=True)
                # Return 500 so Razorpay retries
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error processing webhook: {str(e)}"
                )
        
        elif event_type == "payment.failed":
            # Extract payment details
            razorpay_payment_id = entity.get("id") or event_payload.get("id")
            razorpay_order_id = entity.get("order_id") or event_payload.get("order_id")
            
            if not razorpay_payment_id or not razorpay_order_id:
                logger.error(f"Missing payment_id or order_id in payment.failed webhook: {webhook_data}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing payment_id or order_id in webhook payload"
                )
            
            # Extract payment method details from Razorpay payload (for failed payments too)
            from .Order_crud import extract_payment_method_from_razorpay_payload
            payment_method_details, payment_method_metadata = extract_payment_method_from_razorpay_payload(entity)
            
            # Find payment by razorpay_order_id
            payment = db.query(Payment).filter(
                Payment.razorpay_order_id == razorpay_order_id
            ).order_by(Payment.created_at.desc()).first()
            
            if not payment:
                logger.warning(f"Payment not found for Razorpay order ID: {razorpay_order_id}")
                webhook_log.processed = True
                webhook_log.processing_error = f"Payment not found for Razorpay order ID: {razorpay_order_id}"
                webhook_log.processed_at = now_ist()
                db.commit()
                return WebhookResponse(
                    status="success",
                    message=f"Payment not found for Razorpay order ID: {razorpay_order_id}"
                )
            
            # Update webhook log with payment info
            webhook_log.payment_id = payment.id
            webhook_log.order_id = payment.order_id
            webhook_log.razorpay_order_id = razorpay_order_id
            webhook_log.razorpay_payment_id = razorpay_payment_id
            
            # Get order
            order = db.query(Order).filter(Order.id == payment.order_id).first()
            
            if not order:
                logger.warning(f"Order not found for payment ID: {payment.id}")
                webhook_log.processed = True
                webhook_log.processing_error = f"Order not found for payment ID: {payment.id}"
                webhook_log.processed_at = now_ist()
                db.commit()
                return WebhookResponse(
                    status="success",
                    message=f"Order not found for payment ID: {payment.id}"
                )
            
            # Only update if order is not already confirmed
            # Never downgrade a confirmed order
            if order.order_status == OrderStatus.CONFIRMED:
                logger.warning(f"Received payment.failed webhook for confirmed order {order.order_number}. Ignoring (order already confirmed).")
                webhook_log.processed = True
                webhook_log.processing_error = f"Order {order.order_number} already confirmed. Payment failure event ignored."
                webhook_log.processed_at = now_ist()
                db.commit()
                return WebhookResponse(
                    status="success",
                    message=f"Order {order.order_number} already confirmed. Payment failure event ignored."
                )
            
            # Update payment status to failed using mark_payment_failed_or_cancelled
            # Use savepoint to ensure we can rollback status changes on error
            savepoint = db.begin_nested()
            try:
                order = mark_payment_failed_or_cancelled(
                    db=db,
                    order_id=order.id,
                    payment_status=PaymentStatus.FAILED,
                    reason=f"Payment failed (webhook event: payment.failed). Event ID: {event_id}",
                    payment_method_details=payment_method_details,
                    payment_method_metadata=payment_method_metadata
                )
                
                # Commit the savepoint (status changes are now permanent)
                savepoint.commit()
                
                # Update webhook log as processed
                webhook_log.processed = True
                webhook_log.processed_at = now_ist()
                db.commit()
                
                # Send notification only after commit (avoid race: FCM after DB committed)
                try:
                    send_notification_to_user(
                        db,
                        order.user_id,
                        "Payment failed",
                        f"Payment for order {order.order_number} could not be completed. You can retry payment for this order.",
                        type="warning",
                    )
                except Exception as notif_err:
                    logger.warning("Payment failed notification send error (order %s): %s", order.order_number, notif_err)
                
                logger.info(f"Order {order.order_number} marked as failed by webhook")
                
                return WebhookResponse(
                    status="success",
                    message=f"Order {order.order_number} payment marked as failed"
                )
                
            except Exception as e:
                # Rollback savepoint to undo status changes
                savepoint.rollback()
                # Update webhook log with error (but no status changes were committed)
                webhook_log.processed = False
                webhook_log.processing_error = str(e)
                webhook_log.processed_at = now_ist()
                db.commit()
                logger.error(f"Error processing payment.failed webhook: {e}", exc_info=True)
                # Return 500 so Razorpay retries
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error processing webhook: {str(e)}"
                )
        
        elif event_type == "order.paid":
            # Alternative event for successful payment (when order is paid)
            order_entity = webhook_data.get("payload", {}).get("order", {}).get("entity", {})
            razorpay_order_id = order_entity.get("id")
            
            if not razorpay_order_id:
                logger.error(f"Missing order_id in order.paid webhook: {webhook_data}")
                webhook_log.processed = True
                webhook_log.processing_error = "Missing order_id in webhook payload"
                webhook_log.processed_at = now_ist()
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing order_id in webhook payload"
                )
            
            # Find payment by razorpay_order_id
            payment = db.query(Payment).filter(
                Payment.razorpay_order_id == razorpay_order_id
            ).order_by(Payment.created_at.desc()).first()
            
            if not payment:
                logger.warning(f"Payment not found for Razorpay order ID: {razorpay_order_id}")
                webhook_log.processed = True
                webhook_log.processing_error = f"Payment not found for Razorpay order ID: {razorpay_order_id}"
                webhook_log.processed_at = now_ist()
                db.commit()
                return WebhookResponse(
                    status="success",
                    message=f"Payment not found for Razorpay order ID: {razorpay_order_id}"
                )
            
            # Update webhook log with payment info
            webhook_log.payment_id = payment.id
            webhook_log.order_id = payment.order_id
            webhook_log.razorpay_order_id = razorpay_order_id
            
            # Get order
            order = db.query(Order).filter(Order.id == payment.order_id).first()
            
            if not order:
                logger.warning(f"Order not found for payment ID: {payment.id}")
                webhook_log.processed = True
                webhook_log.processing_error = f"Order not found for payment ID: {payment.id}"
                webhook_log.processed_at = now_ist()
                db.commit()
                return WebhookResponse(
                    status="success",
                    message=f"Order not found for payment ID: {payment.id}"
                )
            
            # Get payment ID - use from database first (stored during frontend verification)
            razorpay_payment_id = payment.razorpay_payment_id
            
            # Get payment ID from the payment entity in the webhook payload (same as payment.captured)
            if not razorpay_payment_id:
                payment_entity_in_order_paid = webhook_data.get("payload", {}).get("payment", {}).get("entity", {})
                razorpay_payment_id = payment_entity_in_order_paid.get("id")
                if razorpay_payment_id:
                    logger.info(f"Extracted razorpay_payment_id from payment entity for order {order.order_number}")
                else:
                    logger.warning(f"No payment ID found in database or webhook for order {order.order_number}")
                    webhook_log.processed = True
                    webhook_log.processing_error = f"No payment ID found in database or webhook for order {order.order_number}"
                    webhook_log.processed_at = now_ist()
                    db.commit()
                    return WebhookResponse(
                        status="success",
                        message=f"No payment ID found in database or webhook for order {order.order_number}"
                    )
            else:
                logger.info(f"Using razorpay_payment_id from database for order {order.order_number}: {razorpay_payment_id}")
            
            webhook_log.razorpay_payment_id = razorpay_payment_id
            
            # Extract payment method details from Razorpay payload (if available in order.paid event)
            # For order.paid, payment details might be in a different location - try to get from payment entity if available
            payment_entity = webhook_data.get("payload", {}).get("payment", {}).get("entity", {})
            from .Order_crud import extract_payment_method_from_razorpay_payload
            payment_method_details, payment_method_metadata = extract_payment_method_from_razorpay_payload(payment_entity)
            
            # Confirm order from webhook (idempotent)
            # Use savepoint to ensure we can rollback status changes on error
            savepoint = db.begin_nested()
            try:
                order = confirm_order_from_webhook(
                    db=db,
                    order_id=order.id,
                    razorpay_order_id=razorpay_order_id,
                    razorpay_payment_id=razorpay_payment_id,
                    webhook_log_id=webhook_log.id,
                    payment_method_details=payment_method_details,
                    payment_method_metadata=payment_method_metadata
                )
                
                # Commit the savepoint (status changes are now permanent)
                savepoint.commit()
                
                # Update webhook log as processed
                webhook_log.processed = True
                webhook_log.processed_at = now_ist()
                db.commit()
                
                logger.info(f"Order {order.order_number} confirmed by webhook (order.paid)")
                
                return WebhookResponse(
                    status="success",
                    message=f"Order {order.order_number} confirmed successfully"
                )
                
            except ValueError as e:
                # Order already confirmed or other validation error
                # Rollback savepoint to undo any partial changes
                savepoint.rollback()
                logger.info(f"Webhook confirmation skipped: {str(e)}")
                # Update webhook log as processed (idempotent)
                webhook_log.processed = True
                webhook_log.processing_error = str(e)
                webhook_log.processed_at = now_ist()
                db.commit()
                # Return 200 OK (idempotent - already processed)
                return WebhookResponse(
                    status="success",
                    message=str(e)
                )
            except Exception as e:
                # Internal error - rollback savepoint to undo status changes
                savepoint.rollback()
                # Update webhook log with error (but no status changes were committed)
                webhook_log.processed = False
                webhook_log.processing_error = str(e)
                webhook_log.processed_at = now_ist()
                db.commit()
                logger.error(f"Error confirming order from webhook: {e}", exc_info=True)
                # Return 500 so Razorpay retries
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error processing webhook: {str(e)}"
                )
        
        else:
            # Unhandled event type - log and return success (don't retry)
            logger.info(f"Unhandled webhook event type: {event_type}")
            webhook_log.processed = True
            webhook_log.processing_error = f"Event {event_type} received but not processed"
            webhook_log.processed_at = now_ist()
            db.commit()
            return WebhookResponse(
                status="success",
                message=f"Event {event_type} received but not processed"
            )
    
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing webhook: {e}", exc_info=True)
        db.rollback()
        # Return 500 so Razorpay retries on unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error processing webhook: {str(e)}"
        )


@router.get("/list")
def get_orders(
    request: Request,
    current_user: User = Depends(get_current_user),
    current_member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """
    Get all confirmed orders for current user (CONFIRMED status and later).
    If a member is selected, shows only orders where that member appears.
    Returns "No orders yet" message if no confirmed orders exist.
    """
    # Get all orders for user with eager loading of payments to avoid N+1 queries
    from sqlalchemy.orm import joinedload
    all_orders = db.query(Order).options(joinedload(Order.payments)).filter(
        Order.user_id == current_user.id
    ).order_by(Order.created_at.desc()).all()
    
    # Filter to only show CONFIRMED and later statuses
    POST_CONFIRMATION_STATUSES = {
        OrderStatus.CONFIRMED,
        OrderStatus.SCHEDULED,
        OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
        OrderStatus.SAMPLE_COLLECTED,
        OrderStatus.SAMPLE_RECEIVED_BY_LAB,
        OrderStatus.TESTING_IN_PROGRESS,
        OrderStatus.REPORT_READY,
        OrderStatus.COMPLETED
    }
    
    orders = [order for order in all_orders if order.order_status in POST_CONFIRMATION_STATUSES]
    
    result = []
    for order in orders:
        # Group order items by product AND group_id to distinguish different packs of same product
        # We'll filter by member later, after checking plan types
        order_items_to_process = order.items
        # 
        # Why this grouping is needed:
        # - When a user orders multiple packs of the same product (e.g., 2 couple packs),
        #   all items have the same product_id but different group_ids
        # - Without group_id in the key, all members from different packs would be grouped together
        # - With group_id, each pack appears as a separate item in the response
        # 
        # Example: 2 couple packs of product_id=10
        #   - Pack 1: group_id="abc123" → members [1, 2] → separate item
        #   - Pack 2: group_id="xyz789" → members [3, 4] → separate item
        #   Without group_id: all 4 members would appear in one item (incorrect)
        from collections import defaultdict
        grouped_items = defaultdict(list)
        for item in order_items_to_process:
            # Extract and validate group_id from snapshot (optimized: parse JSON once)
            snapshot = item.snapshot if item.snapshot else None
            group_id = extract_and_validate_group_id(snapshot)
            
            # Build group key: product_id + group_id + order_id
            # - product_id: identifies the product
            # - group_id: distinguishes different packs of same product (from cart grouping)
            # - order_id: ensures uniqueness across orders
            # 
            # Fallback behavior: If group_id is None (old orders or invalid data),
            # group by product_id only. This maintains backward compatibility.
            if group_id:
                group_key = f"{item.product_id or item.thyrocare_product_id}_{group_id}_{item.order_id}"
            else:
                # Backward compatibility: old orders created before group_id was implemented
                group_key = f"{item.product_id or item.thyrocare_product_id}_{item.order_id}"
            grouped_items[group_key].append(item)
        
        order_items = []
        for group_key, items in grouped_items.items():
            # Skip if group is empty (should not happen, but safety check)
            if not items:
                continue
                
            # Use first item as representative for product info
            # All items in this group share the same product and group_id
            first_item = items[0]
            snapshot = first_item.snapshot if first_item.snapshot else None
            
            # Get product data from snapshot or item
            if snapshot and snapshot.product_data:
                product_data = snapshot.product_data
                product_name = product_data.get("Name", "Unknown")
                product_id = product_data.get("ProductId") or product_data.get("thyrocare_product_id") or first_item.product_id
                plan_type = product_data.get("plan_type")
            else:
                product_name = first_item.product.Name if first_item.product else "Unknown"
                product_id = first_item.product_id or first_item.thyrocare_product_id
                if first_item.product and hasattr(first_item.product.plan_type, 'value'):
                    plan_type = first_item.product.plan_type.value
                elif first_item.product:
                    plan_type = str(first_item.product.plan_type)
                else:
                    plan_type = None
            
            # Extract group_id (already validated during grouping, reuse for response)
            # This avoids re-parsing JSON - group_id was already extracted above
            group_id = extract_and_validate_group_id(snapshot)
            
            # IMPORTANT: Filter logic based on plan type
            # - For couple/family plans: Keep ALL members in the group (show complete plan)
            # - For single plans: Filter to show only selected member's items
            # - Show order if member placed it OR member is in the group
            items_to_display = items
            if current_member:
                # Check if current member placed this order
                member_placed_order = order.placed_by_member_id == current_member.id if order.placed_by_member_id else False
                
                # For couple/family plans, get ALL items from original order for this group
                # to ensure we show the complete plan with all members
                if plan_type and plan_type.lower() in ["couple", "family"]:
                    # Get all items from the original order that belong to this group
                    all_group_items = []
                    for orig_item in order.items:
                        orig_snapshot = orig_item.snapshot if orig_item.snapshot else None
                        orig_group_id = extract_and_validate_group_id(orig_snapshot)
                        if orig_group_id == group_id and orig_item.product_id == first_item.product_id:
                            all_group_items.append(orig_item)
                    
                    # Check if selected member is part of this group
                    member_in_group = any(item.member_id == current_member.id for item in all_group_items)
                    
                    # Show if member placed the order OR member is in the group
                    if not member_placed_order and not member_in_group:
                        # Selected member neither placed the order nor is in this group, skip this group
                        continue
                    
                    # Use all items from the group (keep original copy, show all members)
                    items_to_display = all_group_items
                else:
                    # For single plans, check if member is in the group and filter
                    member_in_group = any(item.member_id == current_member.id for item in items)
                    
                    # Show if member placed the order OR member is in the group
                    if not member_placed_order and not member_in_group:
                        # Selected member neither placed the order nor is in this group, skip this group
                        continue
                    
                    # Filter to show only the selected member's items for single plans
                    items_to_display = [item for item in items if item.member_id == current_member.id]
            
            # Use first item for calculation (all items in group have same price)
            calculation_item = first_item
            
            # Build member_address_map with full details using items_to_display
            member_address_map = []
            member_ids = []
            address_ids = []
            
            for item in items_to_display:
                snapshot = item.snapshot if item.snapshot else None
                
                if snapshot:
                    # Use snapshot data (from time of order confirmation)
                    member_data = snapshot.member_data or {}
                    address_data = snapshot.address_data or {}
                    
                    # Get mobile number from snapshot if present
                    mobile = member_data.get("mobile")
                    
                    member_details = {
                        "member_id": member_data.get("id", item.member_id),
                        "name": member_data.get("name", "Unknown"),
                        "relation": member_data.get("relation"),
                        "age": member_data.get("age"),
                        "gender": member_data.get("gender"),
                        "dob": member_data.get("dob"),
                        "mobile": mobile
                    }
                    
                    address_details = {
                        "address_id": address_data.get("id", item.address_id),
                        "address_label": address_data.get("address_label"),
                        "street_address": address_data.get("street_address"),
                        "landmark": address_data.get("landmark"),
                        "locality": address_data.get("locality"),
                        "city": address_data.get("city"),
                        "state": address_data.get("state"),
                        "postal_code": address_data.get("postal_code"),
                        "country": address_data.get("country")
                    }
                else:
                    # Fallback to original tables
                    member = item.member
                    address = item.address
                    
                    # Get mobile number from member if present
                    mobile = None
                    if member and member.mobile:
                        mobile = member.mobile
                    
                    member_details = {
                        "member_id": member.id if member else item.member_id,
                        "name": member.name if member else "Unknown",
                        "relation": member.relation.value if member and hasattr(member.relation, 'value') else (str(member.relation) if member else None),
                        "age": member.age if member else None,
                        "gender": member.gender if member else None,
                        "dob": to_ist_isoformat(member.dob) if member and member.dob else None,
                        "mobile": mobile
                    }
                    
                    address_details = {
                        "address_id": address.id if address else item.address_id,
                        "address_label": address.address_label if address else None,
                        "street_address": address.street_address if address else None,
                        "landmark": address.landmark if address else None,
                        "locality": address.locality if address else None,
                        "city": address.city if address else None,
                        "state": address.state if address else None,
                        "postal_code": address.postal_code if address else None,
                        "country": address.country if address else None
                    }
                
                # Display fallback: if order was updated to post-payment status but item stayed PENDING (e.g. before item-level sync fix), show order status
                item_status = item.order_status
                if order.order_status in (
                    OrderStatus.SCHEDULED,
                    OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
                    OrderStatus.SAMPLE_COLLECTED,
                    OrderStatus.SAMPLE_RECEIVED_BY_LAB,
                    OrderStatus.TESTING_IN_PROGRESS,
                    OrderStatus.REPORT_READY,
                    OrderStatus.COMPLETED,
                ) and item_status == OrderStatus.PENDING:
                    item_status = order.order_status
                member_address_map.append({
                    "member": member_details,
                    "address": address_details,
                    "order_item_id": item.id,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "order_status": item_status.value if hasattr(item_status, 'value') else str(item_status),
                    "status_updated_at": to_ist_isoformat(item.status_updated_at),
                    "scheduled_date": to_ist_isoformat(item.scheduled_date),
                    "technician_name": item.technician_name,
                    "technician_contact": item.technician_contact
                })
                
                member_ids.append(member_details["member_id"])
                if address_details["address_id"]:
                    address_ids.append(address_details["address_id"])
            
            # Get unique address IDs
            unique_address_ids = list(set(address_ids))
            
            # Calculate total_amount: price is per product group, not per member
            # For couple/family products, there are multiple order items but price is calculated once per product
            # This matches the cart calculation logic: quantity * unit_price (per product, not per member)
            # Sum unit_price across all items in group (handles blood test multi-member correctly)
            item_total_amount = sum(i.unit_price for i in items if i.unit_price)
            if not item_total_amount:
                item_total_amount = calculation_item.quantity * calculation_item.unit_price

            order_items.append({
                "product_id": product_id,
                "product_name": product_name,
                "group_id": group_id,
                "member_ids": list(set(member_ids)),
                "address_ids": unique_address_ids,
                "member_address_map": member_address_map,
                "quantity": calculation_item.quantity,
                "total_amount": item_total_amount
            })

        # Only include this order if it has items after filtering
        if not order_items:
            continue
        
        # Get latest payment record for payment details
        latest_payment = (
            db.query(Payment)
            .filter(Payment.order_id == order.id)
            .order_by(Payment.created_at.desc())
            .first()
        )
        razorpay_order_id = latest_payment.razorpay_order_id if latest_payment else None
        payment_method = (
            latest_payment.payment_method.value
            if latest_payment and hasattr(latest_payment.payment_method, "value")
            else (str(latest_payment.payment_method) if latest_payment else None)
        )
        payment_method_details = (
            latest_payment.payment_method_details if latest_payment else None
        )
        payment_method_metadata = (
            latest_payment.payment_method_metadata if latest_payment else None
        )
        
        result.append({
            "order_number": order.order_number,
            "user_id": order.user_id,
            "address_id": order.address_id,
            "subtotal": order.subtotal,
            "discount": order.discount,
            "coupon_code": order.coupon_code,
            "coupon_discount": order.coupon_discount,
            "delivery_charge": order.delivery_charge,
            "total_amount": order.total_amount,
            "payment_status": order.payment_status.value if hasattr(order.payment_status, 'value') else str(order.payment_status),
            "order_status": order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
            "razorpay_order_id": razorpay_order_id,
            "payment_method": payment_method,
            "payment_method_details": payment_method_details,
            "payment_method_metadata": payment_method_metadata,
            "razorpay_customer_id": getattr(order, "razorpay_customer_id", None),
            "razorpay_invoice_id": getattr(order, "razorpay_invoice_id", None),
            "razorpay_invoice_number": getattr(order, "razorpay_invoice_number", None),
            "razorpay_invoice_url": getattr(order, "razorpay_invoice_url", None),
            "razorpay_invoice_status": getattr(order, "razorpay_invoice_status", None),
            "created_at": to_ist_isoformat(order.created_at),
            "order_date": to_ist_isoformat(order.status_updated_at or order.created_at),
            "items": order_items
        })
    
    # If no confirmed orders found, return message instead of empty list
    if not result:
        return {
            "status": "success",
            "message": "No orders yet",
            "data": []
        }
    
    return result


# @router.get("/tracking", response_model=List[OrderTrackingResponse])
# def get_all_orders_tracking(
#     request: Request,
#     current_user: User = Depends(get_current_user),
#     db: Session = Depends(get_db)
# ):
#     """
#     Get comprehensive tracking information for all orders of the current user.
#     Returns detailed order information including pricing, product/plan breakdown, 
#     address-wise tracking, and member details. Clearly indicates current vs past orders.
#     """
#     from .Order_schema import OrderItemTracking, MemberDetails, AddressDetails
#     from .Order_model import OrderStatus
#     
#     orders = get_user_orders(db, current_user.id)
#     
#     result = []
#     for order in orders:
#         # Build order items list - clean mapping of member, address, product, and status
#         order_items_list = []
#         
#         for item in order.items:
#             snapshot = item.snapshot if item.snapshot else None
#             
#             # Get product details
#             if snapshot and snapshot.product_data:
#                 product_data = snapshot.product_data
#                 product_id = product_data.get("ProductId", item.product_id)
#                 product_name = product_data.get("Name", "Unknown Product")
#                 plan_type = product_data.get("plan_type", None)
#                 unit_price = item.unit_price
#             else:
#                 product_obj = item.product
#                 product_id = product_obj.ProductId if product_obj else item.product_id
#                 product_name = product_obj.Name if product_obj else "Unknown Product"
#                 plan_type = product_obj.plan_type.value if product_obj and hasattr(product_obj.plan_type, 'value') else (str(product_obj.plan_type) if product_obj else None)
#                 unit_price = item.unit_price
#             
#             quantity = item.quantity
#             
#             # Extract and validate group_id from snapshot (optimized: parse JSON once per item)
#             # group_id distinguishes different packs of the same product
#             group_id = extract_and_validate_group_id(snapshot)
#             
#             # Get member details
#             if snapshot and snapshot.member_data:
#                 member_data = snapshot.member_data
#                 member = MemberDetails(
#                     member_id=member_data.get("id", item.member_id),
#                     name=member_data.get("name", "Unknown"),
#                     relation=member_data.get("relation"),
#                     age=member_data.get("age"),
#                     gender=member_data.get("gender"),
#                     dob=member_data.get("dob"),
#                     mobile=member_data.get("mobile")
#                 )
#             else:
#                 member_obj = item.member
#                 relation_val = None
#                 if member_obj:
#                     if hasattr(member_obj.relation, 'value'):
#                         relation_val = member_obj.relation.value
#                     else:
#                         relation_val = str(member_obj.relation) if member_obj.relation else None
#                 
#                 member = MemberDetails(
#                     member_id=member_obj.id if member_obj else item.member_id,
#                     name=member_obj.name if member_obj else "Unknown",
#                     relation=relation_val,
#                     age=member_obj.age if member_obj else None,
#                     gender=member_obj.gender if member_obj else None,
#                     dob=to_ist_isoformat(member_obj.dob) if member_obj and member_obj.dob else None,
#                     mobile=member_obj.mobile if member_obj else None
#                 )
#             
#             # Get address details
#             if snapshot and snapshot.address_data:
#                 address_data = snapshot.address_data
#                 address = AddressDetails(
#                     address_id=address_data.get("id", item.address_id),
#                     address_label=address_data.get("address_label"),
#                     street_address=address_data.get("street_address"),
#                     landmark=address_data.get("landmark"),
#                     locality=address_data.get("locality"),
#                     city=address_data.get("city"),
#                     state=address_data.get("state"),
#                     postal_code=address_data.get("postal_code"),
#                     country=address_data.get("country")
#                 )
#             else:
#                 address_obj = item.address
#                 address = AddressDetails(
#                     address_id=address_obj.id if address_obj else item.address_id,
#                     address_label=address_obj.address_label if address_obj else None,
#                     street_address=address_obj.street_address if address_obj else None,
#                     landmark=address_obj.landmark if address_obj else None,
#                     locality=address_obj.locality if address_obj else None,
#                     city=address_obj.city if address_obj else None,
#                     state=address_obj.state if address_obj else None,
#                     postal_code=address_obj.postal_code if address_obj else None,
#                     country=address_obj.country if address_obj else None
#                 )
#             
#             # Get current and previous status
#             current_status = item.order_status.value if hasattr(item.order_status, 'value') else str(item.order_status)
#             previous_status = None
#             
#             # Get status history for this order item
#             item_status_history = []
#             for hist in order.status_history:
#                 if hist.order_item_id == item.id:
#                     item_status_history.append({
#                         "status": hist.status.value,
#                         "previous_status": hist.previous_status.value if hist.previous_status else None,
#                         "notes": hist.notes,
#                         "created_at": to_ist_isoformat(hist.created_at)
#                     })
#             
#             # Sort status history by created_at (most recent first) for display
#             item_status_history.sort(key=lambda x: x.get("created_at") or "", reverse=True)
#             
#             # Get previous status from the most recent history entry (which contains previous_status)
#             if item_status_history:
#                 previous_status = item_status_history[0].get("previous_status")
#             
#             order_items_list.append(OrderItemTracking(
#                 order_item_id=item.id,
#                 product_id=product_id,
#                 product_name=product_name,
#                 plan_type=str(plan_type).lower() if plan_type else None,
#                 group_id=group_id,  # Group ID to distinguish different packs of same product
#                 quantity=quantity,
#                 unit_price=unit_price,
#                 member=member,
#                 address=address,
#                 current_status=current_status,
#                 previous_status=previous_status,
#                 status_updated_at=item.status_updated_at,
#                 scheduled_date=item.scheduled_date,
#                 technician_name=item.technician_name,
#                 technician_contact=item.technician_contact,
#                 created_at=item.created_at,
#                 status_history=item_status_history
#             ))
#         
#         # Sort order items by product_id to group related items together
#         order_items_list.sort(key=lambda x: (x.product_id or 0, x.order_item_id))
#         
#         # Calculate product discount (difference between subtotal and sum of unit prices)
#         # This represents the discount from product pricing (Price - SpecialPrice)
#         # Sum unique products (since items with same product_id share the same price)
#         seen_products = set()
#         total_product_prices = 0.0
#         for item in order_items_list:
#             product_key = item.product_id
#             if product_key and product_key not in seen_products:
#                 total_product_prices += item.quantity * item.unit_price
#                 seen_products.add(product_key)
#         
#         product_discount = max(0.0, total_product_prices - order.subtotal) if order.subtotal else None
#         
#         result.append(OrderTrackingResponse(
#             order_id=order.id,
#             order_number=order.order_number,
#             order_date=order.created_at,
#             payment_status=order.payment_status.value,
#             current_status=order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
#             subtotal=order.subtotal,
#             product_discount=product_discount,
#             coupon_code=order.coupon_code,
#             coupon_discount=order.coupon_discount,
#             delivery_charge=order.delivery_charge,
#             total_amount=order.total_amount,
#             order_items=order_items_list
#         ))
#     
#     return result


@router.get("/{order_number}", response_model=OrderResponse)
def get_order(
    order_number: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    current_member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """
    Get order details by order number. If a member is selected, shows only order items for that member.
    Allows viewing orders with any payment status (pending/failed/verified) so users can check their order status.
    """
    # Allow viewing orders with any payment status so users can see pending/failed orders
    # Eager load payments to avoid N+1 queries
    from sqlalchemy.orm import joinedload
    order = db.query(Order).options(joinedload(Order.payments)).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )

    thyrocare_extras = _thyrocare_line_extras_factory(db, order.id, list(order.items))
    
    # Group order items by product AND group_id to distinguish different packs of same product
    # We'll filter by member later, after checking plan types
    order_items_to_process = order.items
    # 
    # Why this grouping is needed:
    # - When a user orders multiple packs of the same product (e.g., 2 couple packs),
    #   all items have the same product_id but different group_ids
    # - Without group_id in the key, all members from different packs would be grouped together
    # - With group_id, each pack appears as a separate item in the response
    # 
    # Example: 2 couple packs of product_id=10
    #   - Pack 1: group_id="abc123" → members [1, 2] → separate item
    #   - Pack 2: group_id="xyz789" → members [3, 4] → separate item
    #   Without group_id: all 4 members would appear in one item (incorrect)
    from collections import defaultdict
    grouped_items = defaultdict(list)
    for item in order_items_to_process:
        # Extract and validate group_id from snapshot (optimized: parse JSON once)
        snapshot = item.snapshot if item.snapshot else None
        group_id = extract_and_validate_group_id(snapshot)
        
        # Build group key: product_id + group_id + order_id
        # - product_id: identifies the product
        # - group_id: distinguishes different packs of same product (from cart grouping)
        # - order_id: ensures uniqueness across orders
        # 
        # Fallback behavior: If group_id is None (old orders or invalid data),
        # group by product_id only. This maintains backward compatibility.
        if group_id:
            group_key = f"{item.product_id or item.thyrocare_product_id}_{group_id}_{order.id}"
        else:
            # Backward compatibility: old orders created before group_id was implemented
            group_key = f"{item.product_id or item.thyrocare_product_id}_{order.id}"
        grouped_items[group_key].append(item)
    
    order_items = []
    for group_key, items in grouped_items.items():
        # Skip if group is empty (should not happen, but safety check)
        if not items:
            continue
            
        # Use first item as representative for product info
        # All items in this group share the same product and group_id
        first_item = items[0]
        snapshot = first_item.snapshot if first_item.snapshot else None
        
        # Get product data from snapshot or item
        if snapshot and snapshot.product_data:
            product_data = snapshot.product_data
            product_name = product_data.get("Name", "Unknown")
            product_id = product_data.get("ProductId") or product_data.get("thyrocare_product_id") or first_item.product_id
            plan_type = product_data.get("plan_type")
        else:
            product_name = first_item.product.Name if first_item.product else "Unknown"
            product_id = first_item.product_id or first_item.thyrocare_product_id
            if first_item.product and hasattr(first_item.product.plan_type, 'value'):
                plan_type = first_item.product.plan_type.value
            elif first_item.product:
                plan_type = str(first_item.product.plan_type)
            else:
                plan_type = None
        
        # Extract group_id (already validated during grouping, reuse for response)
        # This avoids re-parsing JSON - group_id was already extracted above
        group_id = extract_and_validate_group_id(snapshot)
        
        # IMPORTANT: Filter logic based on plan type
        # - For couple/family plans: Keep ALL members in the group (show complete plan)
        # - For single plans: Filter to show only selected member's items
        # - Show order if member placed it OR member is in the group
        items_to_display = items
        if current_member:
            # Check if current member placed this order
            member_placed_order = order.placed_by_member_id == current_member.id if order.placed_by_member_id else False
            
            # For couple/family plans, get ALL items from original order for this group
            # to ensure we show the complete plan with all members
            if plan_type and plan_type.lower() in ["couple", "family"]:
                # Get all items from the original order that belong to this group
                all_group_items = []
                for orig_item in order.items:
                    orig_snapshot = orig_item.snapshot if orig_item.snapshot else None
                    orig_group_id = extract_and_validate_group_id(orig_snapshot)
                    if orig_group_id == group_id and orig_item.product_id == first_item.product_id:
                        all_group_items.append(orig_item)
                
                # Check if selected member is part of this group
                member_in_group = any(item.member_id == current_member.id for item in all_group_items)
                
                # Show if member placed the order OR member is in the group
                if not member_placed_order and not member_in_group:
                    # Selected member neither placed the order nor is in this group, skip this group
                    continue
                
                # Use all items from the group (keep original copy, show all members)
                items_to_display = all_group_items
            else:
                # For single plans, check if member is in the group and filter
                member_in_group = any(item.member_id == current_member.id for item in items)
                
                # Show if member placed the order OR member is in the group
                if not member_placed_order and not member_in_group:
                    # Selected member neither placed the order nor is in this group, skip this group
                    continue
                
                # Filter to show only the selected member's items for single plans
                items_to_display = [item for item in items if item.member_id == current_member.id]
        
        # Use first item for calculation (all items in group have same price)
        calculation_item = first_item
        
        # Build member_address_map with full details using items_to_display
        member_address_map = []
        member_ids = []
        address_ids = []
        
        for item in items_to_display:
            snapshot = item.snapshot if item.snapshot else None
            
            if snapshot:
                # Use snapshot data (from time of order confirmation)
                member_data = snapshot.member_data or {}
                address_data = snapshot.address_data or {}
                
                member_details = {
                    "member_id": member_data.get("id", item.member_id),
                    "name": member_data.get("name", "Unknown"),
                    "relation": member_data.get("relation"),
                    "age": member_data.get("age"),
                    "gender": member_data.get("gender"),
                    "dob": member_data.get("dob"),
                    "mobile": member_data.get("mobile")
                }
                
                address_details = {
                    "address_id": address_data.get("id", item.address_id),
                    "address_label": address_data.get("address_label"),
                    "street_address": address_data.get("street_address"),
                    "landmark": address_data.get("landmark"),
                    "locality": address_data.get("locality"),
                    "city": address_data.get("city"),
                    "state": address_data.get("state"),
                    "postal_code": address_data.get("postal_code"),
                    "country": address_data.get("country")
                }
            else:
                # Fallback to original tables
                member = item.member
                address = item.address
                
                member_details = {
                    "member_id": member.id if member else item.member_id,
                    "name": member.name if member else "Unknown",
                    "relation": member.relation.value if member and hasattr(member.relation, 'value') else (str(member.relation) if member else None),
                    "age": member.age if member else None,
                    "gender": member.gender if member else None,
                    "dob": to_ist_isoformat(member.dob) if member and member.dob else None,
                    "mobile": member.mobile if member else None
                }
                
                address_details = {
                    "address_id": address.id if address else item.address_id,
                    "address_label": address.address_label if address else None,
                    "street_address": address.street_address if address else None,
                    "landmark": address.landmark if address else None,
                    "locality": address.locality if address else None,
                    "city": address.city if address else None,
                    "state": address.state if address else None,
                    "postal_code": address.postal_code if address else None,
                    "country": address.country if address else None
                }
            
            # Display fallback: if order was updated to post-payment status but item stayed PENDING (e.g. before item-level sync fix), show order status
            item_status = item.order_status
            if order.order_status in (
                OrderStatus.SCHEDULED,
                OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
                OrderStatus.SAMPLE_COLLECTED,
                OrderStatus.SAMPLE_RECEIVED_BY_LAB,
                OrderStatus.TESTING_IN_PROGRESS,
                OrderStatus.REPORT_READY,
                OrderStatus.COMPLETED,
            ) and item_status == OrderStatus.PENDING:
                item_status = order.order_status
            member_address_map.append({
                "member": member_details,
                "address": address_details,
                "order_item_id": item.id,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "order_status": item_status.value if hasattr(item_status, 'value') else str(item_status),
                "status_updated_at": to_ist_isoformat(item.status_updated_at),
                "scheduled_date": to_ist_isoformat(item.scheduled_date),
                "technician_name": item.technician_name,
                "technician_contact": item.technician_contact,
                **thyrocare_extras(item),
            })
            
            member_ids.append(member_details["member_id"])
            if address_details["address_id"]:
                address_ids.append(address_details["address_id"])
        
        # Get unique address IDs
        unique_address_ids = list(set(address_ids))
        
        # Calculate total_amount: price is per product group, not per member
        # For couple/family products, there are multiple order items but price is calculated once per product
        # This matches the cart calculation logic: quantity * unit_price (per product, not per member)
        # Sum unit_price across all items in group (handles blood test multi-member correctly)
        item_total_amount = sum(i.unit_price for i in items if i.unit_price)
        if not item_total_amount:
            item_total_amount = calculation_item.quantity * calculation_item.unit_price

        _tc_ids_ordered: List[str] = []
        _seen_tc: set = set()
        for _i in items_to_display:
            _tid = _i.thyrocare_order_id
            if _tid:
                _ts = str(_tid).strip()
                if _ts and _ts not in _seen_tc:
                    _seen_tc.add(_ts)
                    _tc_ids_ordered.append(_ts)

        order_items.append({
            "product_id": product_id,
            "product_name": product_name,
            "group_id": group_id,
            "member_ids": list(set(member_ids)),
            "address_ids": unique_address_ids,
            "member_address_map": member_address_map,
            "quantity": calculation_item.quantity,
            "total_amount": item_total_amount,
            "thyrocare_order_ids": _tc_ids_ordered,
        })
    
    # Check if we have any items after filtering
    if not order_items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No order items found for the selected member in this order"
        )
    
    # Get latest payment record for payment details (from eager-loaded relationship)
    latest_payment = max(order.payments, key=lambda p: p.created_at) if order.payments else None
    razorpay_order_id = latest_payment.razorpay_order_id if latest_payment else None
    payment_method = (
        latest_payment.payment_method.value
        if latest_payment and hasattr(latest_payment.payment_method, "value")
        else (str(latest_payment.payment_method) if latest_payment else None)
    )
    payment_method_details = latest_payment.payment_method_details if latest_payment else None
    payment_method_metadata = latest_payment.payment_method_metadata if latest_payment else None
    
    # Get payment timestamps
    payment_confirmed_at = None
    payment_failed_at = None
    if order.payments:
        # Find confirmed payment (payment_date is set when payment is confirmed)
        confirmed_payment = next((p for p in order.payments if p.payment_status == PaymentStatus.COMPLETED and p.payment_date), None)
        if confirmed_payment:
            payment_confirmed_at = confirmed_payment.payment_date
        
        # Find failed payment (updated_at when status is FAILED)
        failed_payment = next((p for p in order.payments if p.payment_status == PaymentStatus.FAILED and p.updated_at), None)
        if failed_payment:
            payment_failed_at = failed_payment.updated_at
    
    # Return OrderResponse directly (not wrapped in status/message/data)
    return {
        "order_number": order.order_number,
        "user_id": order.user_id,
        "address_id": order.address_id,
        "subtotal": order.subtotal,
        "discount": order.discount,
        "coupon_code": order.coupon_code,
        "coupon_discount": order.coupon_discount,
        "delivery_charge": order.delivery_charge,
        "total_amount": order.total_amount,
        "payment_status": order.payment_status.value if hasattr(order.payment_status, 'value') else str(order.payment_status),
        "order_status": order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
        "razorpay_order_id": razorpay_order_id if razorpay_order_id else None,
        "payment_method": payment_method,
        "payment_method_details": payment_method_details,
        "payment_method_metadata": payment_method_metadata,
        "razorpay_customer_id": getattr(order, "razorpay_customer_id", None),
        "razorpay_invoice_id": getattr(order, "razorpay_invoice_id", None),
        "razorpay_invoice_number": getattr(order, "razorpay_invoice_number", None),
        "razorpay_invoice_url": getattr(order, "razorpay_invoice_url", None),
        "razorpay_invoice_status": getattr(order, "razorpay_invoice_status", None),
        "created_at": to_ist_isoformat(order.created_at),
        "order_date": to_ist_isoformat(order.status_updated_at or order.created_at),
        "status_updated_at": to_ist_isoformat(order.status_updated_at),
        "payment_confirmed_at": to_ist_isoformat(payment_confirmed_at),
        "payment_failed_at": to_ist_isoformat(payment_failed_at),
        "items": order_items
    }


@router.get("/{order_number}/tracking")
def get_order_tracking(
    order_number: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    current_member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """
    Get order tracking information for a specific order by order number.
    Only works for orders with CONFIRMED status and later (SCHEDULED, SAMPLE_COLLECTED, etc.).
    If a member is selected, shows only order items for that member.
    Returns "No orders for tracking" if order is not confirmed yet.
    Shows message "The order has been confirmed and processing has begun" when order reaches CONFIRMED.
    """
    from .Order_schema import OrderItemTracking, MemberDetails, AddressDetails
    from .Order_model import OrderStatus
    
    # Verify order exists and belongs to user (allow all payment statuses to check order status)
    # Eager load payments to avoid N+1 queries
    from sqlalchemy.orm import joinedload
    order = db.query(Order).options(joinedload(Order.payments)).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        return {
            "status": "success",
            "message": "No orders for tracking",
            "data": None
        }
    
    # Check if order is confirmed or later (tracking only available for confirmed orders)
    POST_CONFIRMATION_STATUSES = {
        OrderStatus.CONFIRMED,
        OrderStatus.SCHEDULED,
        OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
        OrderStatus.SAMPLE_COLLECTED,
        OrderStatus.SAMPLE_RECEIVED_BY_LAB,
        OrderStatus.TESTING_IN_PROGRESS,
        OrderStatus.REPORT_READY,
        OrderStatus.COMPLETED,
    }
    
    if order.order_status not in POST_CONFIRMATION_STATUSES:
        return {
            "status": "success",
            "message": "No orders for tracking",
            "data": None,
            "order_status": order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
            "payment_status": order.payment_status.value if hasattr(order.payment_status, 'value') else str(order.payment_status)
        }
    
    # Filter order items by member if a member is selected
    # If member placed the order, show all items; otherwise show only items for that member
    order_items_to_process = order.items
    if current_member:
        # Check if current member placed this order
        member_placed_order = order.placed_by_member_id == current_member.id if order.placed_by_member_id else False
        
        if member_placed_order:
            # Member placed the order, show all items
            order_items_to_process = order.items
        else:
            # Member didn't place the order, show only items for that member
            order_items_to_process = [item for item in order.items if item.member_id == current_member.id]
            if not order_items_to_process:
                return {
                    "status": "success",
                    "message": "No orders for tracking",
                    "data": None
                }
    
    # Build order items list - clean mapping of member, address, product, and status
    thyrocare_extras = _thyrocare_line_extras_factory(db, order.id, list(order.items))
    order_items_list = []
    
    for item in order_items_to_process:
        snapshot = item.snapshot if item.snapshot else None
        
        # Get product details
        if snapshot and snapshot.product_data:
            product_data = snapshot.product_data
            product_id = product_data.get("ProductId") or product_data.get("thyrocare_product_id") or item.product_id
            product_name = product_data.get("Name", "Unknown Product")
            plan_type = product_data.get("plan_type", None)
            unit_price = item.unit_price
        else:
            product_obj = item.product
            product_id = product_obj.ProductId if product_obj else (item.product_id or item.thyrocare_product_id)
            product_name = product_obj.Name if product_obj else "Unknown Product"
            plan_type = product_obj.plan_type.value if product_obj and hasattr(product_obj.plan_type, 'value') else (str(product_obj.plan_type) if product_obj else None)
            unit_price = item.unit_price
        
        quantity = item.quantity
        
        # Extract and validate group_id from snapshot (optimized: parse JSON once per item)
        # group_id distinguishes different packs of the same product
        group_id = extract_and_validate_group_id(snapshot)
        
        # Get member details
        if snapshot and snapshot.member_data:
            member_data = snapshot.member_data
            member = MemberDetails(
                member_id=member_data.get("id", item.member_id),
                name=member_data.get("name", "Unknown"),
                relation=member_data.get("relation"),
                age=member_data.get("age"),
                gender=member_data.get("gender"),
                dob=member_data.get("dob"),
                mobile=member_data.get("mobile")
            )
        else:
            member_obj = item.member
            relation_val = None
            if member_obj:
                if hasattr(member_obj.relation, 'value'):
                    relation_val = member_obj.relation.value
                else:
                    relation_val = str(member_obj.relation) if member_obj.relation else None
            
            member = MemberDetails(
                member_id=member_obj.id if member_obj else item.member_id,
                name=member_obj.name if member_obj else "Unknown",
                relation=relation_val,
                age=member_obj.age if member_obj else None,
                gender=member_obj.gender if member_obj else None,
                dob=to_ist_isoformat(member_obj.dob) if member_obj and member_obj.dob else None,
                mobile=member_obj.mobile if member_obj else None
            )
        
        # Get address details
        if snapshot and snapshot.address_data:
            address_data = snapshot.address_data
            address = AddressDetails(
                address_id=address_data.get("id", item.address_id),
                address_label=address_data.get("address_label"),
                street_address=address_data.get("street_address"),
                landmark=address_data.get("landmark"),
                locality=address_data.get("locality"),
                city=address_data.get("city"),
                state=address_data.get("state"),
                postal_code=address_data.get("postal_code"),
                country=address_data.get("country")
            )
        else:
            address_obj = item.address
            address = AddressDetails(
                address_id=address_obj.id if address_obj else item.address_id,
                address_label=address_obj.address_label if address_obj else None,
                street_address=address_obj.street_address if address_obj else None,
                landmark=address_obj.landmark if address_obj else None,
                locality=address_obj.locality if address_obj else None,
                city=address_obj.city if address_obj else None,
                state=address_obj.state if address_obj else None,
                postal_code=address_obj.postal_code if address_obj else None,
                country=address_obj.country if address_obj else None
            )
        
        # Get current and previous status
        current_status = item.order_status.value if hasattr(item.order_status, 'value') else str(item.order_status)
        previous_status = None
        
        # Get status history for this order item
        item_status_history = []
        for hist in order.status_history:
            if hist.order_item_id == item.id:
                item_status_history.append({
                    "status": hist.status.value,
                    "previous_status": hist.previous_status.value if hist.previous_status else None,
                    "notes": hist.notes,
                    "created_at": to_ist_isoformat(hist.created_at)
                })
        
        # Sort status history by created_at (most recent first) for display
        item_status_history.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        
        # Get previous status from the most recent history entry (which contains previous_status)
        if item_status_history:
            previous_status = item_status_history[0].get("previous_status")
        
        order_items_list.append(OrderItemTracking(
            order_item_id=item.id,
            product_id=product_id,
            product_name=product_name,
            plan_type=str(plan_type).lower() if plan_type else None,
            group_id=group_id,  # Group ID to distinguish different packs of same product
            quantity=quantity,
            unit_price=unit_price,
            member=member,
            address=address,
            current_status=current_status,
            previous_status=previous_status,
            status_updated_at=to_ist_isoformat(item.status_updated_at),
            scheduled_date=to_ist_isoformat(item.scheduled_date),
            technician_name=item.technician_name,
            technician_contact=item.technician_contact,
            created_at=to_ist_isoformat(item.created_at),
            status_history=item_status_history,
            **thyrocare_extras(item),
        ))
    
    # Sort order items by product_id to group related items together
    order_items_list.sort(key=lambda x: (x.product_id or 0, x.order_item_id))
    
    # Calculate product discount — use order_item_id as dedup key since product_id is None for blood tests
    seen_items = set()
    total_product_prices = 0.0
    for item in order_items_list:
        if item.order_item_id not in seen_items:
            total_product_prices += item.unit_price
            seen_items.add(item.order_item_id)
    
    product_discount = max(0.0, total_product_prices - order.subtotal) if order.subtotal else None
    
    # Get payment timestamps
    payment_confirmed_at = None
    payment_failed_at = None
    if order.payments:
        # Find confirmed payment (payment_date is set when payment is confirmed)
        confirmed_payment = next((p for p in order.payments if p.payment_status == PaymentStatus.COMPLETED and p.payment_date), None)
        if confirmed_payment:
            payment_confirmed_at = confirmed_payment.payment_date
        
        # Find failed payment (updated_at when status is FAILED)
        failed_payment = next((p for p in order.payments if p.payment_status == PaymentStatus.FAILED and p.updated_at), None)
        if failed_payment:
            payment_failed_at = failed_payment.updated_at
    
    # Add confirmation message when order reaches CONFIRMED status
    confirmation_message = None
    if order.order_status == OrderStatus.CONFIRMED:
        confirmation_message = "The order has been confirmed and processing has begun"
    
    return {
        "status": "success",
        "message": confirmation_message,
        "data": {
            "order_id": order.id,
            "order_number": order.order_number,
            "order_date": to_ist_isoformat(order.status_updated_at or order.created_at),
            "payment_status": order.payment_status.value if hasattr(order.payment_status, 'value') else str(order.payment_status),
            "current_status": order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
            "subtotal": order.subtotal,
            "product_discount": product_discount,
            "coupon_code": order.coupon_code,
            "coupon_discount": order.coupon_discount,
            "delivery_charge": order.delivery_charge,
            "total_amount": order.total_amount,
            "status_updated_at": to_ist_isoformat(order.status_updated_at),
            "payment_confirmed_at": to_ist_isoformat(payment_confirmed_at),
            "payment_failed_at": to_ist_isoformat(payment_failed_at),
            "order_items": [item.dict() for item in order_items_list]
        }
    }


@router.get("/{order_number}/{order_item_id}/tracking", response_model=OrderItemTrackingResponse)
def get_order_item_tracking(
    order_number: str,
    order_item_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    current_member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """
    Get tracking information for a specific order item.
    If a member is selected, verifies the order item belongs to that member.
    Returns detailed status history, member, address, and product information for the order item.
    """
    from .Order_model import OrderItem
    
    # Verify order exists and belongs to user (allow all payment statuses like get_order endpoint)
    order = get_order_by_number(db, order_number, user_id=current_user.id, include_all_payment_statuses=True)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    
    # Get the specific order item
    # Allow access if:
    # 1. Current user is the user who placed the order (always allowed), OR
    # 2. Current member (if selected) is the member for whom the order was placed
    
    query_filters = [
        OrderItem.id == order_item_id,
        OrderItem.order_id == order.id
    ]
    
    # Build access conditions: user who placed OR member for whom it was placed
    access_conditions = [OrderItem.user_id == current_user.id]
    if current_member:
        # If viewing as a specific member, also allow access if order item is for that member
        access_conditions.append(OrderItem.member_id == current_member.id)
        # Filter results to only show items for this member when member context is active
        query_filters.append(OrderItem.member_id == current_member.id)
    
    # Apply access control: user OR member match
    # This ensures the endpoint works for both the user who placed and the member for whom it was placed
    query_filters.append(or_(*access_conditions))
    
    order_item = db.query(OrderItem).filter(*query_filters).first()
    
    if not order_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order item not found"
        )
    
    # Get snapshot data
    snapshot = order_item.snapshot if order_item.snapshot else None
    
    # Get member data from snapshot or item
    if snapshot and snapshot.member_data:
        member_data = snapshot.member_data
        member = {
            "member_id": member_data.get("id", order_item.member_id),
            "name": member_data.get("name", "Unknown"),
            "relation": member_data.get("relation"),
            "age": member_data.get("age"),
            "gender": member_data.get("gender"),
            "dob": member_data.get("dob"),
            "mobile": member_data.get("mobile")
        }
    else:
        # Fallback to original member
        member_obj = order_item.member
        member = {
            "member_id": member_obj.id if member_obj else order_item.member_id,
            "name": member_obj.name if member_obj else "Unknown",
            "relation": member_obj.relation.value if member_obj and hasattr(member_obj.relation, 'value') else (str(member_obj.relation) if member_obj else None),
            "age": member_obj.age if member_obj else None,
            "gender": member_obj.gender if member_obj else None,
            "dob": to_ist_isoformat(member_obj.dob) if member_obj and member_obj.dob else None,
            "mobile": member_obj.mobile if member_obj else None
        }
    
    # Get address data from snapshot or item
    if snapshot and snapshot.address_data:
        address_data = snapshot.address_data
        address = {
            "address_id": address_data.get("id", order_item.address_id),
            "address_label": address_data.get("address_label"),
            "street_address": address_data.get("street_address"),
            "landmark": address_data.get("landmark"),
            "locality": address_data.get("locality"),
            "city": address_data.get("city"),
            "state": address_data.get("state"),
            "postal_code": address_data.get("postal_code"),
            "country": address_data.get("country")
        }
    else:
        # Fallback to original address
        address_obj = order_item.address
        address = {
            "address_id": address_obj.id if address_obj else order_item.address_id,
            "address_label": address_obj.address_label if address_obj else None,
            "street_address": address_obj.street_address if address_obj else None,
            "landmark": address_obj.landmark if address_obj else None,
            "locality": address_obj.locality if address_obj else None,
            "city": address_obj.city if address_obj else None,
            "state": address_obj.state if address_obj else None,
            "postal_code": address_obj.postal_code if address_obj else None,
            "country": address_obj.country if address_obj else None
        }
    
    # Get product data from snapshot or item
    if snapshot and snapshot.product_data:
        product_data = snapshot.product_data
        product = {
            "product_id": product_data.get("ProductId") or product_data.get("thyrocare_product_id") or order_item.product_id,
            "name": product_data.get("Name", "Unknown"),
            "price": product_data.get("Price") or product_data.get("ListingPrice"),
            "special_price": product_data.get("SpecialPrice") or product_data.get("SellingPrice"),
            "plan_type": product_data.get("plan_type") or product_data.get("product_type"),
            "category": product_data.get("category"),
            "images": product_data.get("Images")
        }
    else:
        # Fallback to original product
        product_obj = order_item.product
        product = {
            "product_id": product_obj.ProductId if product_obj else order_item.product_id,
            "name": product_obj.Name if product_obj else "Unknown",
            "price": product_obj.Price if product_obj else None,
            "special_price": product_obj.SpecialPrice if product_obj else None,
            "plan_type": product_obj.plan_type.value if product_obj and hasattr(product_obj.plan_type, 'value') else (str(product_obj.plan_type) if product_obj else None),
            "category": {
                "id": product_obj.category.id if product_obj and product_obj.category else None,
                "name": product_obj.category.name if product_obj and product_obj.category else None
            } if product_obj else None,
            "images": product_obj.Images if product_obj else None
        }
    
    # Get status history for this order item
    item_status_history = []
    for hist in order.status_history:
        hist_item_id = getattr(hist, 'order_item_id', None)
        if hist_item_id == order_item_id:
            item_status_history.append({
                "status": hist.status.value,
                "previous_status": hist.previous_status.value if hist.previous_status else None,
                "notes": hist.notes,
                "created_at": to_ist_isoformat(hist.created_at)
            })
    
    # Sort status history by created_at (most recent first) for display
    item_status_history.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    
    thyrocare_extras = _thyrocare_line_extras_factory(db, order.id, list(order.items))
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "order_item_id": order_item.id,
        "current_status": order_item.order_status.value if hasattr(order_item.order_status, 'value') else str(order_item.order_status),
        "status_updated_at": to_ist_isoformat(order_item.status_updated_at),
        "status_history": item_status_history,
        "member": member,
        "address": address,
        "product": product,
        "quantity": order_item.quantity,
        "unit_price": order_item.unit_price,
        "scheduled_date": to_ist_isoformat(order_item.scheduled_date),
        "technician_name": order_item.technician_name,
        "technician_contact": order_item.technician_contact,
        **thyrocare_extras(order_item),
    }


@router.put("/{order_number}/status")
def update_order_status_api(
    order_number: str,
    status_data: UpdateOrderStatusRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(verify_order_status_password)
):
    """
    Update order status. Password required in header X-Order-Status-Password (default: 4567).
    Status transitions are flexible - can update from any stage to any stage.
    
    Supports updating:
    - Order-level status (if order_item_id and address_id are both omitted, updates entire order)
    - Order-item level status (if order_item_id is provided, updates specific item and syncs order status)
    - Items by address (if address_id is provided, updates all items with that address and syncs order status)
    
    Valid statuses: CART, PENDING, PENDING_PAYMENT, PROCESSING, PAYMENT_FAILED, CONFIRMED, COMPLETED,
    SCHEDULED, SCHEDULE_CONFIRMED_BY_LAB, SAMPLE_COLLECTED, SAMPLE_RECEIVED_BY_LAB, TESTING_IN_PROGRESS, REPORT_READY
    
    Request body:
    - status: New status to transition to
    - notes: Notes about the status change
    - order_item_id (optional): Update specific order item only (order status will be synced)
    - address_id (optional): Update all items with this address (order status will be synced)
    - scheduled_date (optional): Scheduled date for technician visit (only needed for statuses like 'scheduled')
    - technician_name (optional): Technician name (only needed for statuses like 'scheduled', 'sample_collected')
    - technician_contact (optional): Technician contact (only needed for statuses like 'scheduled', 'sample_collected')
    
    Note: Technician details (scheduled_date, technician_name, technician_contact) are optional.
    They are typically required for: scheduled, schedule_confirmed_by_lab, sample_collected
    They are NOT needed for: confirmed, sample_received_by_lab, testing_in_progress, report_ready
    
    When order_item_id is provided, the specific item is updated and order status is synced based on all items.
    When address_id is provided, all items with that address are updated and order status is synced.
    If neither is provided, both order-level status and all items are updated.
    """
    try:
        # Validate status
        try:
            new_status = OrderStatus(status_data.status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_data.status}. Valid statuses: {[s.value for s in OrderStatus]}"
            )
        
        # Verify order exists by order_number (no user check - no authorization required)
        from sqlalchemy.orm import joinedload
        order = db.query(Order).options(joinedload(Order.items)).filter(Order.order_number == order_number).first()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order not found with order number: {order_number}"
            )
        
        # Validate order_item_id belongs to this order if provided
        if status_data.order_item_id:
            order_item = db.query(OrderItem).filter(
                OrderItem.id == status_data.order_item_id,
                OrderItem.order_id == order.id
            ).first()
            if not order_item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Order item {status_data.order_item_id} not found in order {order_number}"
                )
        
        changed_by = "system"
        
        # Get previous status before update
        previous_order_status = order.order_status
        previous_status_updated_at = order.status_updated_at
        
        # Capture previous item statuses before update
        previous_item_statuses = {}
        items_to_update = []
        
        if status_data.order_item_id:
            # Single item update
            item = next((item for item in order.items if item.id == status_data.order_item_id), None)
            if item:
                previous_item_statuses[item.id] = item.order_status
                items_to_update = [item]
        elif status_data.address_id:
            # Items with specific address
            for item in order.items:
                if item.address_id == status_data.address_id:
                    previous_item_statuses[item.id] = item.order_status
                    items_to_update.append(item)
        else:
            # All items (when updating entire order)
            for item in order.items:
                previous_item_statuses[item.id] = item.order_status
                items_to_update = order.items
        
        # Update status (supports per-item, per-address, or entire order updates)
        # Status transitions are flexible - no validation of sequential progression
        order, notif_payload = update_order_status(
            db=db,
            order_id=order.id,
            new_status=new_status,
            changed_by=changed_by,
            notes=status_data.notes,
            order_item_id=status_data.order_item_id,
            address_id=status_data.address_id,
            scheduled_date=status_data.scheduled_date,
            technician_name=status_data.technician_name,
            technician_contact=status_data.technician_contact
        )
        
        # Send notification only after commit (update_order_status already commits)
        if notif_payload:
            title, message, ntype = notif_payload
            try:
                send_notification_to_user(db, order.user_id, title, message, type=ntype)
            except Exception as notif_err:
                logger.warning("Order status notification send error (order %s): %s", order.order_number, notif_err)
        
        # Refresh order to get updated items
        db.refresh(order)
        
        # Get updated order items information with previous statuses
        updated_items = []
        for item in items_to_update:
            # Refresh item to get latest status
            db.refresh(item)
            updated_items.append({
                "order_item_id": item.id,
                "current_status": item.order_status.value if hasattr(item.order_status, 'value') else str(item.order_status),
                "previous_status": previous_item_statuses.get(item.id).value if previous_item_statuses.get(item.id) and hasattr(previous_item_statuses.get(item.id), 'value') else (str(previous_item_statuses.get(item.id)) if previous_item_statuses.get(item.id) else None),
                "status_updated_at": to_ist_isoformat(item.status_updated_at),
                "scheduled_date": to_ist_isoformat(item.scheduled_date),
                "technician_name": item.technician_name,
                "technician_contact": item.technician_contact
            })
        
        # Get the latest status history entries for this update
        from .Order_model import OrderStatusHistory
        from sqlalchemy import or_, and_
        
        # Get latest order-level history (if entire order was updated)
        latest_order_history = None
        if not status_data.order_item_id and not status_data.address_id:
            latest_order_history = db.query(OrderStatusHistory).filter(
                and_(
                    OrderStatusHistory.order_id == order.id,
                    OrderStatusHistory.order_item_id == None
                )
            ).order_by(OrderStatusHistory.created_at.desc()).first()
        
        # Get latest item-level history entries for updated items
        item_history_entries = []
        if items_to_update:
            item_ids = [item.id for item in items_to_update]
            
            # Get most recent history for each item
            for item_id in item_ids:
                latest_item_history = db.query(OrderStatusHistory).filter(
                    and_(
                        OrderStatusHistory.order_id == order.id,
                        OrderStatusHistory.order_item_id == item_id
                    )
                ).order_by(OrderStatusHistory.created_at.desc()).first()
                
                if latest_item_history:
                    item_history_entries.append({
                        "order_item_id": item_id,
                        "status": latest_item_history.status.value if hasattr(latest_item_history.status, 'value') else str(latest_item_history.status),
                        "previous_status": latest_item_history.previous_status.value if latest_item_history.previous_status and hasattr(latest_item_history.previous_status, 'value') else (str(latest_item_history.previous_status) if latest_item_history.previous_status else None),
                        "notes": latest_item_history.notes,
                        "changed_by": latest_item_history.changed_by,
                        "created_at": to_ist_isoformat(latest_item_history.created_at)
                    })
        
        # Use order-level history if available, otherwise use item-level
        status_history_info = None
        if latest_order_history:
            status_history_info = {
                "status": latest_order_history.status.value if hasattr(latest_order_history.status, 'value') else str(latest_order_history.status),
                "previous_status": latest_order_history.previous_status.value if latest_order_history.previous_status and hasattr(latest_order_history.previous_status, 'value') else (str(latest_order_history.previous_status) if latest_order_history.previous_status else None),
                "notes": latest_order_history.notes,
                "changed_by": latest_order_history.changed_by,
                "created_at": to_ist_isoformat(latest_order_history.created_at)
            }
        elif item_history_entries:
            # If single item updated, return its history
            # If multiple items updated, return the first item's history as representative
            status_history_info = item_history_entries[0] if item_history_entries else None
        
        return {
            "status": "success",
            "message": f"Order status updated to {new_status.value}",
            "order_id": order.id,
            "order_number": order.order_number,
            "current_status": order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status),
            "previous_status": previous_order_status.value if hasattr(previous_order_status, 'value') else str(previous_order_status),
            "status_updated_at": to_ist_isoformat(order.status_updated_at),
            "previous_status_updated_at": to_ist_isoformat(previous_status_updated_at),
            "updated_items": updated_items,
            "status_history": status_history_info,
            "item_status_history": item_history_entries if len(item_history_entries) > 1 else None  # Only include if multiple items updated
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating order status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating order status: {str(e)}"
        )


@router.post("/{order_number}/schedule")
def schedule_order(
    order_number: str,
    schedule_data: ScheduleOrderRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Schedule tests for an order.
    
    Only works for orders that belong to the current user.
    Requires payment_status = COMPLETED (post-payment scheduling).
    
    **Same Slot for All Members JSON (use_same_slot_for_all = true):**
    ```json
    {
      "use_same_slot_for_all": true,
      "scheduled_date": "2026-02-15",
      "slot": "9_11",
      "member_schedules": null
    }
    ```
    
    **Per-Member Scheduling JSON (use_same_slot_for_all = false):**
    ```json
    {
      "use_same_slot_for_all": false,
      "scheduled_date": null,
      "slot": null,
      "member_schedules": [
        {
          "member_id": 63,
          "scheduled_date": "2026-02-15",
          "slot": "9_11"
        },
        {
          "member_id": 64,
          "scheduled_date": "2026-02-16",
          "slot": "15_19"
        }
      ]
    }
    ```
    
    **Note:** Date format should be YYYY-MM-DD. Slot format can be:
    - Morning: "9_11", 
    - Evening: "15_19"
    """
    from .Order_crud import schedule_order_items

    try:
        client_ip = get_client_info(request) if request else None

        # Load order and ensure it belongs to the current user
        from sqlalchemy.orm import joinedload

        order = (
            db.query(Order)
            .options(joinedload(Order.items))
            .filter(
                Order.order_number == order_number,
                Order.user_id == current_user.id,
            )
            .first()
        )

        if not order:
            logger.warning(
                f"Schedule request failed - Order not found or does not belong to user | "
                f"Order number: {order_number} | User ID: {current_user.id} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found",
            )

        # Payment must be completed before scheduling
        if order.payment_status != PaymentStatus.COMPLETED:
            logger.warning(
                f"Schedule request rejected - Payment not completed | "
                f"Order: {order_number} | Payment status: {order.payment_status} | User ID: {current_user.id}"
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "We can schedule your tests only after the payment is completed. "
                    "Please complete the payment first."
                ),
            )

        # Build member_schedule_map: member_id -> {scheduled_date, slot}
        member_schedule_map: Dict[int, Dict[str, Any]] = {}

        if schedule_data.use_same_slot_for_all:
            if not schedule_data.scheduled_date or not schedule_data.slot:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Please select a date and time slot to schedule all tests.",
                )

            # Apply to all members in this order (for items with member_id)
            member_ids = {
                item.member_id
                for item in order.items
                if item.member_id is not None
            }

            if not member_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="No members found in this order to schedule.",
                )

            for member_id in member_ids:
                member_schedule_map[member_id] = {
                    "scheduled_date": schedule_data.scheduled_date,
                    "slot": schedule_data.slot,
                }
        else:
            # Per-member scheduling
            if not schedule_data.member_schedules:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Please provide schedules for each member or choose the same slot for all.",
                )

            for ms in schedule_data.member_schedules:
                member_schedule_map[ms.member_id] = {
                    "scheduled_date": ms.scheduled_date,
                    "slot": ms.slot,
                }

        # Apply scheduling via helper
        order, notif_payload = schedule_order_items(
            db=db,
            order=order,
            member_schedule_map=member_schedule_map,
            changed_by=str(current_user.id),
            notes="Sample collection scheduled",
        )

        # Send "Sample scheduled" notification when order status became SCHEDULED
        if notif_payload:
            title, message, ntype = notif_payload
            try:
                send_notification_to_user(db, order.user_id, title, message, type=ntype)
            except Exception as notif_err:
                logger.warning("Schedule notification send error (order %s): %s", order.order_number, notif_err)

        # Build simple response summarizing schedules
        scheduled_items: List[Dict[str, Any]] = []
        for item in order.items:
            if item.member_id is None:
                continue
            scheduled_items.append(
                {
                    "order_item_id": item.id,
                    "member_id": item.member_id,
                    "scheduled_date": to_ist_isoformat(item.scheduled_date),
                    "status": item.order_status.value
                    if hasattr(item.order_status, "value")
                    else str(item.order_status),
                }
            )

        return {
            "status": "success",
            "message": "Your test has been scheduled successfully.",
            "data": {
                "order_number": order.order_number,
                "order_status": order.order_status.value
                if hasattr(order.order_status, "value")
                else str(order.order_status),
                "scheduled_items": scheduled_items,
            },
        }
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(
            f"Schedule request validation error for order {order_number}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    except Exception as e:
        logger.error(
            f"Error scheduling order {order_number}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong while scheduling your tests. Please try again.",
        )


# ==================== DIAGNOSTIC & INVOICE RETRY ENDPOINTS ====================


@router.get("/debug/razorpay-invoice-test")
async def test_razorpay_invoice_creation(
    current_user: User = Depends(get_current_user),
):
    """
    Test Razorpay invoice API with minimal payload.
    Helps diagnose permission issues and API configuration.
    """
    try:
        # Step 1: Create test customer
        test_customer = razorpay_client.customer.create(data={
            "name": "Test Customer",
            "email": "test@nucleotide.com"
        })

        # Step 2: Create test invoice
        test_invoice = razorpay_client.invoice.create(data={
            "type": "invoice",
            "customer_id": test_customer["id"],
            "currency": "INR",
            "line_items": [{
                "name": "Test Order",
                "amount": 100,
                "currency": "INR",
                "quantity": 1
            }]
        })

        return {
            "status": "success",
            "message": "Invoice API is working correctly",
            "test_customer_id": test_customer.get("id"),
            "test_invoice_id": test_invoice.get("id"),
            "test_invoice_status": test_invoice.get("status"),
            "test_invoice_url": test_invoice.get("short_url")
        }

    except razorpay.errors.BadRequestError as e:
        return {
            "status": "error",
            "error_type": "BadRequestError",
            "message": str(e),
            "possible_causes": [
                "Invoice feature not enabled in Razorpay account",
                "API key lacks invoice creation permissions",
                "Check Razorpay Dashboard > Settings > Invoices",
                "Check Razorpay Dashboard > Settings > API Keys > Permissions"
            ]
        }
    except razorpay.errors.ServerError as e:
        return {
            "status": "error",
            "error_type": "ServerError",
            "message": str(e),
            "possible_causes": [
                "Razorpay API is experiencing issues",
                "This may be temporary - try again later"
            ]
        }
    except Exception as e:
        logger.error(f"Razorpay invoice test failed: {e}", exc_info=True)
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "message": str(e)
        }


@router.post("/orders/{order_id}/retry-invoice")
async def retry_invoice_creation(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Manually retry invoice creation for a confirmed order.
    Useful when invoice creation fails during webhook processing.
    """
    # Get order with user validation
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Validate order state
    if order.order_status != OrderStatus.CONFIRMED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only create invoice for confirmed orders. Current status: {order.order_status}"
        )

    if order.razorpay_invoice_id:
        raise HTTPException(
            status_code=400,
            detail=f"Invoice already exists: {order.razorpay_invoice_id}"
        )

    if not order.razorpay_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No Razorpay customer_id found. Cannot create invoice."
        )

    # Retry invoice creation
    try:
        logger.info(f"Manual invoice retry for order {order.order_number} (ID: {order.id}) by user {current_user.id}")

        invoice = create_razorpay_invoice_for_order(
            customer_id=order.razorpay_customer_id,
            order_number=order.order_number,
            total_amount=order.total_amount,
            currency="INR",
            email=order.user.email if order.user else None
        )

        # Update order with invoice details
        order.razorpay_invoice_id = invoice.get("id")
        order.razorpay_invoice_number = invoice.get("invoice_number") or invoice.get("number")
        order.razorpay_invoice_url = invoice.get("short_url") or invoice.get("invoice_url") or invoice.get("pdf_url")
        order.razorpay_invoice_status = invoice.get("status")

        db.commit()
        db.refresh(order)

        logger.info(f"Manual invoice retry successful for order {order.order_number}: {order.razorpay_invoice_id}")

        return {
            "status": "success",
            "message": "Invoice created successfully",
            "invoice_id": order.razorpay_invoice_id,
            "invoice_number": order.razorpay_invoice_number,
            "invoice_url": order.razorpay_invoice_url,
            "invoice_status": order.razorpay_invoice_status
        }

    except ValueError as e:
        # Log and return error details
        logger.error(f"Manual invoice retry failed for order {order.order_number}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Invoice creation failed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error during manual invoice retry for order {order.order_number}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again or contact support."
        )


@router.post("/test-invoice-email/{order_id}")
async def test_invoice_email_generation(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    TEST ENDPOINT: Manually trigger custom PDF invoice generation and email sending.
    Use this to debug why invoice emails aren't being sent after Razorpay order confirmation.

    This endpoint:
    1. Finds the order (must be CONFIRMED status)
    2. Generates the custom PDF invoice (same as webhook flow)
    3. Sends it via Gmail API (same as webhook flow)
    4. Returns detailed success/failure information

    Usage: POST /orders/test-invoice-email/{order_id}
    """
    # Get order with user validation
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Validate order state
    if order.order_status != OrderStatus.CONFIRMED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only test invoice email for confirmed orders. Current status: {order.order_status.value}"
        )

    # Check if user has email
    if not order.user or not order.user.email:
        raise HTTPException(
            status_code=400,
            detail="Order user has no email address. Cannot send invoice email."
        )

    logger.info(f"TEST: Manual invoice email generation for order {order.order_number} (ID: {order.id}) by user {current_user.id}")

    # Extract the invoice generation logic from confirm_order_from_webhook
    try:
        import sys
        import os
        from pathlib import Path
        from config import settings

        # Ensure the 'invoice generation' folder is in the system path for imports
        project_root = Path(__file__).parent.parent
        invoice_gen_path = project_root / "invoice generation"
        if str(invoice_gen_path) not in sys.path:
            sys.path.append(str(invoice_gen_path))

        from nucleotide_invoice_sender_wo_file import generate_and_send_invoice, build_email_body

        # Prepare invoice data mapping (same as webhook)
        customer_address_str = ""
        if order.address:
            addr_parts = []
            if order.address.street_address: addr_parts.append(order.address.street_address)
            if order.address.city: addr_parts.append(order.address.city)
            if order.address.state: addr_parts.append(order.address.state)
            if order.address.postal_code: addr_parts.append(order.address.postal_code)
            customer_address_str = ", ".join(addr_parts)

        # Build items list (Product Summary only)
        # Group by group_id so couple/family packs appear as one line per pack,
        # not one line per member. If no group_id, fall back to item id.
        _groups: dict = {}
        for item in order.items:
            item_name = "Genetic Test Product"
            if item.snapshot and item.snapshot.product_data:
                item_name = item.snapshot.product_data.get("Name", item_name)
            elif item.product:
                item_name = item.product.Name

            group_key = None
            if item.snapshot and item.snapshot.cart_item_data:
                group_key = item.snapshot.cart_item_data.get("group_id")
            group_key = group_key or str(item.id)

            if group_key not in _groups:
                _groups[group_key] = {"name": item_name, "amount": 0}
            _groups[group_key]["amount"] += item.unit_price if item.unit_price else 0
        invoice_items = list(_groups.values())

        # Prepare payment info — use the most recent COMPLETED payment, fallback to latest
        payment_info = []
        payment = (
            db.query(Payment)
            .filter(Payment.order_id == order.id, Payment.payment_status == PaymentStatus.COMPLETED)
            .order_by(Payment.created_at.desc())
            .first()
        ) or (
            db.query(Payment)
            .filter(Payment.order_id == order.id)
            .order_by(Payment.created_at.desc())
            .first()
        )
        if payment:
            mode = "Razorpay"
            if payment.payment_method_details:
                mode = f"Razorpay ({payment.payment_method_details.upper()})"

            payment_info.append({
                "mode": mode,
                "reference": payment.razorpay_payment_id or "N/A",
                "amount": payment.amount
            })

        # Build the final data dictionary exactly as expected by nucleotide_invoice
        invoice_data = {
            "invoice_number": order.razorpay_invoice_id or order.razorpay_invoice_number or f"INV-{order.order_number}",
            "invoice_date": now_ist().strftime("%B %d, %Y"),
            "order_number": order.order_number,
            "sac_code": settings.INVOICE_SAC_CODE,

            "company_name": settings.INVOICE_COMPANY_NAME,
            "company_address": settings.INVOICE_COMPANY_ADDRESS,
            "pan_number": settings.INVOICE_PAN_NUMBER,

            "customer_name": order.user.name or "Valued Customer",
            "customer_address": customer_address_str,

            "items": invoice_items,
            "detailed_items": [], # Omitted as requested

            "total_amount": order.subtotal,
            "grand_total": order.total_amount,
            "paid_amount": sum(p["amount"] for p in payment_info) if payment_info else 0,
            "discount_coupon": {
                "code": order.coupon_code,
                "amount": order.coupon_discount
            } if order.coupon_code else None,

            "payment_info": payment_info,
            "customer_care_phone": settings.INVOICE_CUSTOMER_CARE_PHONE,
            "customer_care_email": settings.INVOICE_CUSTOMER_CARE_EMAIL,
            "website": settings.INVOICE_WEBSITE
        }

        # Build plain-text and HTML email bodies
        plain_body, html_body = build_email_body(invoice_data)

        # Resolve logo path (relative to root)
        logo_path = str(project_root / settings.INVOICE_LOGO_PATH)
        if not os.path.exists(logo_path):
            logger.warning(f"TEST: Invoice logo not found at {logo_path}. Falling back to text logo.")
            logo_path = None

        # Send the invoice email
        result = generate_and_send_invoice(
            invoice_data=invoice_data,
            logo_path=logo_path,
            to=order.user.email,
            subject=f"Order Confirmation & Invoice – {order.order_number}",
            body=plain_body,
            html_body=html_body,
            pdf_filename=f"Invoice-{order.order_number}.pdf",
            service_account_file=str(project_root / settings.INVOICE_SERVICE_ACCOUNT_PATH),
            sender_email=settings.INVOICE_SENDER_EMAIL
        )

        logger.info(f"TEST: Custom branded PDF invoice sent to {order.user.email} for order {order.order_number}")

        return {
            "status": "success",
            "message": "Invoice email sent successfully",
            "order_number": order.order_number,
            "recipient_email": order.user.email,
            "invoice_number": invoice_data["invoice_number"],
            "logo_found": logo_path is not None,
            "email_result": result,
            "debug_info": {
                "order_id": order.id,
                "user_id": current_user.id,
                "order_status": order.order_status.value,
                "total_amount": order.total_amount,
                "customer_name": invoice_data["customer_name"],
                "item_count": len(invoice_items),
                "has_payment_info": len(payment_info) > 0
            }
        }

    except ImportError as e:
        logger.error(f"TEST: Import error for invoice generation: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Invoice generation module not found: {str(e)}. Check if 'invoice generation' folder exists."
        )

    except FileNotFoundError as e:
        logger.error(f"TEST: File not found error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Required file not found: {str(e)}. Check service account file and logo path."
        )

    except Exception as e:
        logger.error(f"TEST: Error in invoice email generation for order {order.order_number}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Invoice email generation failed: {str(e)}. Check logs for detailed error."
        )
