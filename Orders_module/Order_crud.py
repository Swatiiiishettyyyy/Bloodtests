"""
Order CRUD operations.
"""
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, time, timezone
from typing import Optional, List, Tuple, Dict, Any
from Login_module.Utils.datetime_utils import now_ist, to_ist_isoformat
from .Order_model import (
    Order, OrderItem, OrderSnapshot, OrderStatusHistory,
    Payment, PaymentTransition, WebhookLog,
    OrderStatus, PaymentStatus, PaymentMethod
)
from .order_number_service import generate_order_number
from Cart_module.Cart_model import CartItem
from Cart_module.coupon_service import get_applied_coupon, validate_and_calculate_discount
from Product_module.Product_model import Product
from Member_module.Member_model import Member
from Address_module.Address_model import Address
from config import settings
import logging

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency; used for order-status notifications
def _send_order_notification(db: Session, order: Order, title: str, message: str, type: str = "info") -> None:
    try:
        from Notification_module.Notification_crud import send_notification_to_user
        send_notification_to_user(db, order.user_id, title, message, type=type)
    except Exception as e:
        logger.warning("Order notification failed (order %s): %s", order.order_number, e)


def _get_slot_time(slot: str) -> time:
    """
    Map a slot label to a representative time of day.
    Slots are UX labels (e.g., MORNING, EVENING) corresponding to fixed windows:
    - MORNING: 09:00–11:00 (we store 09:00)
    - EVENING: 15:00–19:00 (we store 15:00)
    """
    if not slot:
        raise ValueError("Slot is required")

    slot_upper = slot.strip().upper()

    if slot_upper in ["MORNING", "9_11", "9-11", "09_11", "09-11"]:
        return time(hour=9, minute=0)

    if slot_upper in ["EVENING", "15_19", "15-19", "3_7", "3-7"]:
        return time(hour=15, minute=0)

    raise ValueError(f"Invalid slot value: {slot}. Allowed: MORNING or EVENING")


def schedule_order_items(
    db: Session,
    order: Order,
    member_schedule_map: Dict[int, Dict[str, Any]],
    changed_by: str = "system",
    notes: Optional[str] = None,
) -> Tuple[Order, Optional[Tuple[str, str, str]]]:
    """
    Apply SCHEDULED status and scheduled_date per order item using update_order_status.
    Returns (order, notif_payload) so the caller can send "Sample scheduled" notification.

    member_schedule_map format:
        {
            member_id: {
                "date": date,          # datetime.date
                "slot": str,           # slot label (MORNING/EVENING)
            },
            ...
        }

    - Only items whose member_id appears in the map will be updated.
    - Uses a representative time for the slot (see _get_slot_time).
    - Uses update_order_status to ensure consistency and history tracking.
    """
    if not member_schedule_map:
        raise ValueError("member_schedule_map cannot be empty")

    # Ensure payment is completed before scheduling (post-payment statuses)
    if order.payment_status != PaymentStatus.COMPLETED:
        raise ValueError(
            f"Cannot schedule order {order.order_number} because payment status is "
            f"{order.payment_status.value if hasattr(order.payment_status, 'value') else str(order.payment_status)}. "
            f"Payment must be completed before scheduling."
        )

    last_notif_payload: Optional[Tuple[str, str, str]] = None

    # For each member_id, schedule all items in this order that belong to that member
    for member_id, schedule in member_schedule_map.items():
        schedule_date: date = schedule.get("scheduled_date")
        slot: str = schedule.get("slot")

        if not schedule_date or not slot:
            raise ValueError(f"Both date and slot are required for member_id={member_id}")

        # Reject past dates
        today = now_ist().date()
        if schedule_date < today:
            raise ValueError(f"Scheduled date {schedule_date} for member_id={member_id} cannot be in the past")

        slot_time = _get_slot_time(slot)
        scheduled_dt = datetime.combine(schedule_date, slot_time, tzinfo=timezone.utc)

        # Find items for this member in this order
        member_items = [item for item in order.items if item.member_id == member_id]
        if not member_items:
            # Skip silently or raise? For now, raise to surface potential frontend issues
            raise ValueError(
                f"No order items found for member_id={member_id} in order {order.order_number}"
            )

        # Update each item separately to keep per-item history clean
        for item in member_items:
            order, notif_payload = update_order_status(
                db=db,
                order_id=order.id,
                new_status=OrderStatus.SCHEDULED,
                changed_by=changed_by,
                notes=notes or f"Scheduled visit for member_id={member_id} on {schedule_date} ({slot}).",
                order_item_id=item.id,
                scheduled_date=scheduled_dt,
            )
            if notif_payload is not None:
                last_notif_payload = notif_payload

    # Refresh order after all updates to get latest status
    db.refresh(order)
    return order, last_notif_payload


def sync_order_item_statuses_with_order_status(
    db: Session,
    order: Order,
    order_status: OrderStatus,
    update_timestamp: bool = True
) -> None:
    """
    Sync all order item statuses based on the order-level status.
    Logic:
    - If order status is CONFIRMED → item status = CONFIRMED
    - If order status is PAYMENT_FAILED → item status = PAYMENT_FAILED
    - If order status is in post-payment pipeline (SCHEDULED, SCHEDULE_CONFIRMED_BY_LAB,
      SAMPLE_COLLECTED, SAMPLE_RECEIVED_BY_LAB, TESTING_IN_PROGRESS, REPORT_READY) → item status = order status
    - For all other order statuses (PENDING_PAYMENT, PROCESSING, etc.) → item status = PENDING

    Args:
        db: Database session
        order: Order object with items relationship loaded
        order_status: The order-level status
        update_timestamp: Whether to update status_updated_at timestamp
    """
    POST_PAYMENT_ITEM_SYNC = {
        OrderStatus.SCHEDULED,
        OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
        OrderStatus.SAMPLE_COLLECTED,
        OrderStatus.SAMPLE_RECEIVED_BY_LAB,
        OrderStatus.TESTING_IN_PROGRESS,
        OrderStatus.REPORT_READY,
    }
    if order_status == OrderStatus.CONFIRMED:
        item_status = OrderStatus.CONFIRMED
    elif order_status == OrderStatus.PAYMENT_FAILED:
        item_status = OrderStatus.PAYMENT_FAILED
    elif order_status in POST_PAYMENT_ITEM_SYNC:
        item_status = order_status
    else:
        item_status = OrderStatus.PENDING

    # Update all order items
    for item in order.items:
        item.order_status = item_status
        if update_timestamp:
            item.status_updated_at = now_ist()


def extract_payment_method_from_razorpay_payload(entity: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Extract payment method details from Razorpay webhook payload.
    
    Args:
        entity: The payment entity from Razorpay webhook payload
        
    Returns:
        tuple: (payment_method_details, payment_method_metadata)
        - payment_method_details: e.g., "upi", "netbanking", "wallet", "card", "emi", etc.
        - payment_method_metadata: Additional details like VPA, bank name, wallet name, card details, etc.
    """
    if not entity:
        return None, None
    
    # Extract payment method (e.g., "upi", "netbanking", "wallet", "card", "emi")
    payment_method = entity.get("method")
    
    # Build metadata dictionary with relevant payment method details
    metadata = {}
    
    if payment_method:
        # UPI payment details
        if payment_method == "upi":
            metadata["vpa"] = entity.get("vpa")  # Virtual Payment Address
            metadata["upi_provider"] = entity.get("acquirer_data", {}).get("upi", {}).get("payer_account_type") if entity.get("acquirer_data") else None
        
        # Netbanking payment details
        elif payment_method == "netbanking":
            metadata["bank"] = entity.get("bank")
            metadata["bank_transaction_id"] = entity.get("acquirer_data", {}).get("bank_transaction_id") if entity.get("acquirer_data") else None
        
        # Wallet payment details
        elif payment_method == "wallet":
            metadata["wallet"] = entity.get("wallet")
            metadata["wallet_provider"] = entity.get("wallet")  # e.g., "paytm", "mobikwik", "freecharge"
        
        # Card payment details
        elif payment_method == "card":
            card_data = entity.get("card", {})
            if card_data:
                metadata["card_id"] = card_data.get("id")
                metadata["card_network"] = card_data.get("network")  # e.g., "Visa", "MasterCard", "RuPay"
                metadata["card_type"] = card_data.get("type")  # e.g., "credit", "debit"
                metadata["card_last4"] = card_data.get("last4")
                metadata["card_issuer"] = card_data.get("issuer")
        
        # EMI payment details
        elif payment_method == "emi":
            metadata["emi"] = entity.get("emi", {})
            metadata["bank"] = entity.get("bank")
        
        # Add other common fields
        if entity.get("acquirer_data"):
            metadata["acquirer"] = entity.get("acquirer_data", {}).get("acquirer")
            metadata["auth_code"] = entity.get("acquirer_data", {}).get("auth_code")
    
    return payment_method, metadata if metadata else None


def find_existing_order_for_retry(
    db: Session,
    user_id: int,
    cart_item_ids: List[int]
) -> Optional[Order]:
    """
    Find existing order for retry payment scenario.
    Looks for orders with PAYMENT_FAILED or PENDING_PAYMENT status
    that have the same cart items (same products, members, addresses).
    
    Returns the most recent matching order, or None if not found.
    """
    # Get cart items to match against
    cart_items = (
        db.query(CartItem)
        .filter(
            CartItem.id.in_(cart_item_ids),
            CartItem.user_id == user_id,
            CartItem.is_deleted == False
        )
        .all()
    )
    
    if not cart_items:
        return None
    
    # Build set of cart item signatures for matching
    # Use thyrocare_product_id for blood test items, product_id for genetic items
    cart_signatures = {
        (item.product_id, item.thyrocare_product_id, item.member_id, item.address_id, item.quantity)
        for item in cart_items
    }
    
    # Find orders for this user with retry-able statuses
    # Order by created_at DESC to get most recent first
    candidate_orders = (
        db.query(Order)
        .filter(
            Order.user_id == user_id,
            Order.order_status.in_([OrderStatus.PAYMENT_FAILED, OrderStatus.PENDING_PAYMENT])
        )
        .order_by(Order.created_at.desc())
        .all()
    )
    
    # Check each candidate order to see if it matches current cart items
    for order in candidate_orders:
        # Get order items for this order
        order_items = (
            db.query(OrderItem)
            .filter(OrderItem.order_id == order.id)
            .all()
        )
        
        if len(order_items) != len(cart_items):
            # Different number of items, can't match
            continue
        
        # Build set of order item signatures
        order_signatures = {
            (item.product_id, item.thyrocare_product_id, item.member_id, item.address_id, item.quantity)
            for item in order_items
        }
        
        # Check if signatures match exactly
        if cart_signatures == order_signatures:
            # Found matching order - return it
            logger.info(f"Found existing order {order.order_number} for retry payment")
            return order
    
    # No matching order found
    return None


def create_order_from_cart(
    db: Session,
    user_id: int,
    address_id: Optional[int],
    cart_item_ids: List[int],
    razorpay_order_id: Optional[str] = None,
    placed_by_member_id: Optional[int] = None,
    prevalidated_coupon_code: Optional[str] = None,
    prevalidated_coupon_discount: float = 0.0,
) -> Order:
    """
    Create order from cart items.
    Creates snapshots of products, members, and addresses at time of order.
    """
    from Cart_module.Cart_model import ProductType as PT
    # Validate all cart items belong to user (exclude deleted items)
    cart_items = (
        db.query(CartItem)
        .filter(
            CartItem.id.in_(cart_item_ids),
            CartItem.user_id == user_id,
            CartItem.is_deleted == False  # Exclude deleted items
        )
        .all()
    )
    
    if len(cart_items) != len(cart_item_ids):
        raise ValueError("One or more cart items not found or do not belong to you")
    
    if not cart_items:
        raise ValueError("No cart items selected")
    
    # Get unique addresses from cart items
    unique_cart_address_ids = {item.address_id for item in cart_items if item.address_id}
    
    if not unique_cart_address_ids:
        raise ValueError("No addresses associated with selected cart items")
    
    # Determine primary address_id
    primary_address_id = None
    
    if address_id:
        # If address_id is provided, validate it
        if address_id not in unique_cart_address_ids:
            raise ValueError(
                f"Address ID {address_id} is not associated with any of the selected cart items. "
                f"Cart items use address IDs: {sorted(unique_cart_address_ids)}"
            )
        primary_address_id = address_id
    else:
        # If address_id is not provided, use the first address from cart items
        primary_address_id = next(iter(sorted(unique_cart_address_ids)))
        logger.info(
            f"Address ID not provided for order creation. Using first address from cart items: {primary_address_id}. "
            f"All addresses in cart: {sorted(unique_cart_address_ids)}"
        )
    
    # Verify the address belongs to the user and exists
    address = db.query(Address).filter(
        Address.id == primary_address_id,
        Address.user_id == user_id,
        Address.is_deleted == False
    ).first()
    
    if not address:
        raise ValueError(f"Address ID {primary_address_id} not found or does not belong to you")
    
    # Log if multiple addresses are involved (for tracking purposes)
    if len(unique_cart_address_ids) > 1:
        logger.info(
            "Order creation for user %s contains multiple addresses (%s); using %s as primary",
            user_id,
            sorted(unique_cart_address_ids),
            primary_address_id
        )
    else:
        logger.info(
            "Order creation for user %s uses single address %s for all items",
            user_id,
            primary_address_id
        )
    
    # Calculate totals
    subtotal = 0.0
    delivery_charge = 0.0  # Free delivery
    discount = 0.0
    coupon_discount = 0.0
    coupon_code = None
    
    # Group cart items by group_id to calculate totals correctly
    grouped_items = {}
    for item in cart_items:
        group_key = item.group_id or f"single_{item.id}"
        if group_key not in grouped_items:
            grouped_items[group_key] = []
        grouped_items[group_key].append(item)
    
    # Calculate subtotal and discount (price is per product, not per member)
    # For couple/family products, there are multiple cart item rows but discount applies once per product
    # This matches the cart calculation logic exactly
    for group_key, items in grouped_items.items():
        # Skip if group is empty (should not happen, but safety check)
        if not items:
            continue
            
        item = items[0]  # Use first item as representative
        product = item.product
        
        # Skip blood test items — they are booked via Thyrocare after payment
        if getattr(item, 'product_type', None) == PT.BLOOD_TEST:
            thyrocare_product = item.thyrocare_product
            if not thyrocare_product:
                raise ValueError(f"Blood test cart item {item.id} has no associated Thyrocare product")
            # Do NOT query Thyrocare cart/price-breakup for net payable. Use catalog selling_price per member.
            subtotal += float(thyrocare_product.selling_price or 0) * len(items)
            continue

        # Skip if product is deleted or missing
        if not product:
            continue
            
        # Only count once per product group
        subtotal += item.quantity * product.SpecialPrice
        
        # Calculate discount once per product group (same logic as cart view)
        discount_per_item = product.Price - product.SpecialPrice
        discount += discount_per_item * item.quantity
    
    # Use pre-validated coupon from router if provided (avoids double-validation
    # which can falsely reject coupons due to usage already recorded from prior orders).
    # Otherwise fall back to re-validating from the cart.
    if prevalidated_coupon_code and prevalidated_coupon_discount > 0:
        coupon_code = prevalidated_coupon_code
        coupon_discount = prevalidated_coupon_discount
        logger.info(f"Order will include pre-validated coupon '{coupon_code}' with discount of ₹{coupon_discount}")
    else:
        applied_coupon = get_applied_coupon(db, user_id)
        if applied_coupon:
            # Coupons apply to genetic test items only
            from Cart_module.Cart_model import ProductType as _PT
            genetic_subtotal = sum(
                item.quantity * item.product.SpecialPrice
                for item in cart_items
                if getattr(item, 'product_type', None) != _PT.BLOOD_TEST and item.product
            )
            coupon, calculated_discount, error_message = validate_and_calculate_discount(
                db, applied_coupon.coupon_code, user_id, genetic_subtotal, cart_items
            )

            if coupon and not error_message:
                coupon_discount = calculated_discount
                coupon_code = applied_coupon.coupon_code
                logger.info(f"Order will include coupon '{coupon_code}' with discount of ₹{coupon_discount} (recalculated from subtotal ₹{subtotal})")
            else:
                logger.warning(f"Coupon '{applied_coupon.coupon_code}' failed validation during order creation: {error_message}. Coupon will not be applied.")
                coupon_discount = 0.0
                coupon_code = None
    
    # Calculate total amount
    # Note: subtotal already uses SpecialPrice (product discount is already applied)
    # So we only subtract coupon_discount, not discount
    # This matches the corrected cart calculation: grand_total = subtotal_amount + delivery_charge - coupon_amount
    total_amount = subtotal + delivery_charge - coupon_discount
    # Ensure total is not negative
    total_amount = max(0.0, total_amount)
    
    # Create order (without payment fields - payment is in separate table)
    order = Order(
        order_number=generate_order_number(db),
        user_id=user_id,
        placed_by_member_id=placed_by_member_id,  # Member profile that was active when order was placed
        address_id=primary_address_id,
        subtotal=subtotal,
        delivery_charge=delivery_charge,
        discount=discount,
        coupon_code=coupon_code,
        coupon_discount=coupon_discount,
        total_amount=total_amount,
        payment_status=PaymentStatus.PENDING,  # Order created, payment not started (denormalized for quick queries)
        order_status=OrderStatus.PENDING_PAYMENT  # Order created, waiting for payment
    )
    db.add(order)
    db.flush()  # Get order.id
    
    # Create payment record (always create, even if razorpay_order_id is None initially)
    # This ensures every order has a payment record for consistency
    # Note: razorpay_order_id is NOT NULL in the database, so we use empty string as placeholder
    payment = Payment(
        order_id=order.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.PENDING,
        razorpay_order_id=razorpay_order_id or "",  # Use empty string if None (will be updated when Razorpay order is created)
        amount=total_amount,
        currency="INR",
        notes="Initial payment record created with order" + (f" (Razorpay order ID: {razorpay_order_id})" if razorpay_order_id else " (Razorpay order ID pending)")
    )
    db.add(payment)
    db.flush()  # Get payment.id
    
    # Create initial payment transition
    payment_transition = PaymentTransition(
        payment_id=payment.id,
        from_status=None,  # Initial status
        to_status=PaymentStatus.PENDING,
        transition_reason="Order created, payment not started",
        triggered_by="system"
    )
    db.add(payment_transition)
    
    # If razorpay_order_id was provided later, update the payment record
    if razorpay_order_id and payment.razorpay_order_id != razorpay_order_id:
        payment.razorpay_order_id = razorpay_order_id
        payment.notes = f"Initial payment record created with order (Razorpay order ID: {razorpay_order_id})"
        db.flush()
    
    def _blood_test_unit_price(cart_item) -> float:
        return float(cart_item.thyrocare_product.selling_price or 0)

    # Create order items and snapshots
    for cart_item in cart_items:
        is_blood_test = getattr(cart_item, 'product_type', None) == PT.BLOOD_TEST

        if is_blood_test:
            thyrocare_product = cart_item.thyrocare_product
            member = cart_item.member
            address_obj = cart_item.address
            if not thyrocare_product or not member or not address_obj:
                raise ValueError(f"Missing data for blood test cart item {cart_item.id}")
            if not cart_item.appointment_date or not cart_item.appointment_start_time:
                raise ValueError(
                    f"Appointment date/time not set for blood test cart item {cart_item.id} "
                    f"(group_id={cart_item.group_id})"
                )

            snapshot = OrderSnapshot(
                order_id=order.id,
                user_id=user_id,
                product_data={
                    "product_type": "blood_test",
                    "thyrocare_product_id": thyrocare_product.id,
                    "thyrocare_id": thyrocare_product.thyrocare_id,
                    "Name": thyrocare_product.name,
                    "SellingPrice": thyrocare_product.selling_price,
                    "ListingPrice": thyrocare_product.listing_price,
                    "appointment_date": str(cart_item.appointment_date),
                    "appointment_start_time": cart_item.appointment_start_time,
                },
                member_data={
                    "id": member.id,
                    "name": member.name,
                    "relation": member.relation.value if hasattr(member.relation, 'value') else str(member.relation),
                    "age": member.age,
                    "gender": member.gender,
                    "dob": to_ist_isoformat(member.dob) if member.dob else None,
                    "mobile": member.mobile,
                },
                address_data={
                    "id": address_obj.id,
                    "address_label": address_obj.address_label,
                    "street_address": address_obj.street_address,
                    "landmark": address_obj.landmark,
                    "locality": address_obj.locality,
                    "city": address_obj.city,
                    "state": address_obj.state,
                    "postal_code": address_obj.postal_code,
                    "country": address_obj.country,
                },
                cart_item_data={"group_id": cart_item.group_id, "product_type": "blood_test"},
            )
            db.add(snapshot)
            db.flush()

            order_item = OrderItem(
                order_id=order.id,
                user_id=user_id,
                product_id=None,
                thyrocare_product_id=thyrocare_product.id,
                member_id=member.id,
                address_id=cart_item.address_id,
                snapshot_id=snapshot.id,
                quantity=1,
                unit_price=_blood_test_unit_price(cart_item),
                order_status=OrderStatus.PENDING_PAYMENT,
            )
            db.add(order_item)
            db.flush()

            item_status_history = OrderStatusHistory(
                order_id=order.id,
                order_item_id=order_item.id,
                status=OrderStatus.PENDING_PAYMENT,
                previous_status=None,
                notes=f"Blood test order item created for member {member.name}. Waiting for payment.",
                changed_by=str(user_id),
            )
            db.add(item_status_history)
            continue

        # --- Genetic test item (original logic) ---
        product = cart_item.product
        member = cart_item.member
        address_obj = cart_item.address
        
        # Validate required relationships exist
        if not product:
            raise ValueError(f"Product not found for cart item {cart_item.id}")
        if not member:
            raise ValueError(f"Member not found for cart item {cart_item.id}")
        if not address_obj:
            raise ValueError(f"Address not found for cart item {cart_item.id}")
        
        # Create snapshot
        snapshot = OrderSnapshot(
            order_id=order.id,
            user_id=user_id,
            product_data={
                "ProductId": product.ProductId,
                "Name": product.Name,
                "Price": product.Price,
                "SpecialPrice": product.SpecialPrice,
                "plan_type": product.plan_type.value if hasattr(product.plan_type, 'value') else str(product.plan_type),
                "category": {
                    "id": product.category.id if product.category else None,
                    "name": product.category.name if product.category else None,
                },
                "ShortDescription": product.ShortDescription,
                "Images": product.Images
            },
            member_data={
                "id": member.id,
                "name": member.name,
                "relation": member.relation.value if hasattr(member.relation, 'value') else str(member.relation),
                "age": member.age,
                "gender": member.gender,
                "dob": to_ist_isoformat(member.dob) if member.dob else None,
                "mobile": member.mobile
            },
            address_data={
                "id": address_obj.id,
                "address_label": address_obj.address_label,
                "street_address": address_obj.street_address,
                "landmark": address_obj.landmark,
                "locality": address_obj.locality,
                "city": address_obj.city,
                "state": address_obj.state,
                "postal_code": address_obj.postal_code,
                "country": address_obj.country
            },
            cart_item_data={
                "group_id": cart_item.group_id,  # Distinguish different packs of same product
                # Genetic test per-member slot — captured at order time so it survives cart clear
                "appointment_date": str(cart_item.appointment_date) if cart_item.appointment_date else None,
                "appointment_start_time": cart_item.appointment_start_time,
                "appointment_end_time": cart_item.appointment_end_time,
            }
        )
        db.add(snapshot)
        db.flush()
        
        # Create order item with initial status
        order_item = OrderItem(
            order_id=order.id,
            user_id=user_id,
            product_id=product.ProductId,
            member_id=member.id,
            address_id=cart_item.address_id,
            snapshot_id=snapshot.id,
            quantity=cart_item.quantity,
            unit_price=product.SpecialPrice,  # Store SpecialPrice as unit_price
            order_status=OrderStatus.PENDING_PAYMENT  # Initialize with pending payment status
        )
        db.add(order_item)
        db.flush()  # Get order_item.id for status history
        
        # Create initial status history for this order item
        item_status_history = OrderStatusHistory(
            order_id=order.id,
            order_item_id=order_item.id,
            status=OrderStatus.PENDING_PAYMENT,
            previous_status=None,
            notes=f"Order item created for member {member.name} at address {address_obj.address_label or address_obj.id}. Waiting for payment.",
            changed_by=str(user_id)
        )
        db.add(item_status_history)
    
        # Create initial status history
    status_history = OrderStatusHistory(
        order_id=order.id,
        status=OrderStatus.PENDING_PAYMENT,
        previous_status=None,
        notes="Order created from cart. Waiting for payment.",
        changed_by=str(user_id)
    )
    db.add(status_history)
    
    db.commit()
    db.refresh(order)
    
    return order


def verify_payment_frontend(
    db: Session,
    order_id: int,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str
) -> Order:
    """
    Verify Razorpay payment from frontend (verify-payment endpoint).
    ONLY sets payment_status = SUCCESS (temporary).
    DOES NOT confirm order or clear cart.
    Webhook will finalize confirmation later.
    """
    # Load order with items
    from sqlalchemy.orm import joinedload
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    
    # Refresh to get latest state (prevent race conditions)
    db.refresh(order)
    
    # If webhook already confirmed, do nothing (idempotent)
    if order.order_status == OrderStatus.CONFIRMED:
        logger.info(f"Order {order.order_number} already confirmed by webhook. Frontend verification skipped.")
        return order
    
    # If already verified by frontend, return success (idempotent)
    if order.payment_status == PaymentStatus.PROCESSING:
        logger.info(f"Payment already verified for order {order.order_number}. Returning success.")
        return order
    
    # If payment already failed, raise error
    if order.payment_status == PaymentStatus.FAILED:
        raise ValueError("Payment for this order has already failed. Please create a new order to retry payment.")
    
    # Get the latest payment for this order
    # First try to find payment with matching razorpay_order_id
    payment = db.query(Payment).filter(
        Payment.order_id == order.id,
        Payment.razorpay_order_id == razorpay_order_id
    ).order_by(Payment.created_at.desc()).first()
    
    # If not found, get the latest payment for this order (fallback)
    if not payment:
        payment = db.query(Payment).filter(
            Payment.order_id == order.id
        ).order_by(Payment.created_at.desc()).first()
        
        if not payment:
            # Create payment record if it doesn't exist (shouldn't happen, but handle gracefully)
            logger.warning(f"Payment record not found for order {order.order_number}. Creating new payment record.")
            payment = Payment(
                order_id=order.id,
                payment_method=PaymentMethod.RAZORPAY,
                payment_status=PaymentStatus.PENDING,
                razorpay_order_id=razorpay_order_id,
                amount=order.total_amount,
                currency="INR",
                notes=f"Payment record created during verification (Razorpay order ID: {razorpay_order_id})"
            )
            db.add(payment)
            db.flush()
        else:
            # Update existing payment with razorpay_order_id if it's missing
            if not payment.razorpay_order_id or payment.razorpay_order_id != razorpay_order_id:
                payment.razorpay_order_id = razorpay_order_id
                db.flush()
    
    # Validate razorpay_order_id matches
    if payment.razorpay_order_id != razorpay_order_id:
        raise ValueError(f"Razorpay order ID mismatch. Expected {payment.razorpay_order_id}, got {razorpay_order_id}")
    
    # Verify signature
    from .razorpay_service import verify_payment_signature
    is_valid = verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)
    
    if not is_valid:
        # Update payment status to failed
        previous_payment_status = payment.payment_status
        payment.payment_status = PaymentStatus.FAILED
        payment.razorpay_payment_id = razorpay_payment_id
        payment.razorpay_signature = razorpay_signature
        payment.updated_at = now_ist()
        
        # Create payment transition
        payment_transition = PaymentTransition(
            payment_id=payment.id,
            from_status=previous_payment_status,
            to_status=PaymentStatus.FAILED,
            transition_reason="Payment verification failed. Invalid payment signature.",
            triggered_by="system"
        )
        db.add(payment_transition)
        
        # Update order status
        order.payment_status = PaymentStatus.FAILED  # Denormalized
        previous_order_status = order.order_status
        order.order_status = OrderStatus.PAYMENT_FAILED
        order.status_updated_at = now_ist()
        
        # Update item statuses - sync with order status
        item_previous_statuses = {}
        for item in order.items:
            item_previous_statuses[item.id] = item.order_status
        
        # Sync all order item statuses with order status
        sync_order_item_statuses_with_order_status(db, order, OrderStatus.PAYMENT_FAILED, update_timestamp=True)
        
        # Create status history
        status_history = OrderStatusHistory(
            order_id=order.id,
            status=OrderStatus.PAYMENT_FAILED,
            previous_status=previous_order_status,
            notes="Payment verification failed. Invalid payment signature. Payment status set to FAILED. Order status set to PAYMENT_FAILED.",
            changed_by="system"
        )
        db.add(status_history)
        
        # Create status history for each order item
        for item in order.items:
            item_previous_status = item_previous_statuses.get(item.id, OrderStatus.PENDING_PAYMENT)
            item_status_history = OrderStatusHistory(
                order_id=order.id,
                order_item_id=item.id,
                status=OrderStatus.PAYMENT_FAILED,
                previous_status=item_previous_status,
                notes="Payment verification failed. Order item payment failed.",
                changed_by="system"
            )
            db.add(item_status_history)
        
        db.commit()
        _send_order_notification(
            db, order,
            "Payment failed",
            f"Payment for order {order.order_number} could not be completed. You can retry payment for this order.",
            type="warning"
        )
        logger.warning(f"Payment verification failed for order {order.order_number} (ID: {order.id}). Payment status set to FAILED.")
        raise ValueError("Payment verification failed. Please check your payment details and try again. If the problem persists, please contact support.")
    
    # Payment signature valid - update to PROCESSING (frontend verified, waiting for webhook)
    previous_payment_status = payment.payment_status
    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.payment_status = PaymentStatus.PROCESSING
    payment.updated_at = now_ist()
    
    # Create payment transition
    payment_transition = PaymentTransition(
        payment_id=payment.id,
        from_status=previous_payment_status,
        to_status=PaymentStatus.PROCESSING,
        transition_reason="Frontend payment verification successful. Waiting for webhook confirmation.",
        triggered_by="system"
    )
    db.add(payment_transition)
    
    # Update order (denormalized payment_status)
    order.payment_status = PaymentStatus.PROCESSING
    order.order_status = OrderStatus.PROCESSING  # Frontend verified, waiting for webhook
    order.status_updated_at = now_ist()
    
    # Sync all order item statuses with order status
    sync_order_item_statuses_with_order_status(db, order, OrderStatus.PROCESSING, update_timestamp=True)
    
    # DO NOT clear cart - webhook will handle this
    
    db.commit()
    db.refresh(order)
    
    logger.info(f"Frontend payment verification successful for order {order.order_number}. Payment status: PROCESSING. Order status: PROCESSING. Waiting for webhook confirmation.")
    
    return order


def confirm_order_from_webhook(
    db: Session,
    order_id: int,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    webhook_log_id: Optional[int] = None,
    payment_method_details: Optional[str] = None,
    payment_method_metadata: Optional[dict] = None
) -> Order:
    """
    Confirm order from webhook (payment.captured event).
    This is the ONLY place where order confirmation happens.
    Idempotent - can be called multiple times safely.
    """
    # Load order with items using row-level locking to prevent race conditions
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from sqlalchemy import func as sql_func
    
    # Use SELECT FOR UPDATE to lock the row
    # Note: .unique() is required when using joinedload on collections to deduplicate parent rows
    order_stmt = select(Order).options(joinedload(Order.items)).where(Order.id == order_id).with_for_update()
    result = db.execute(order_stmt)
    order = result.scalars().unique().first()
    
    if not order:
        raise ValueError(f"Order {order_id} not found")
    
    # Get the payment record for this order
    # First try to find payment with matching razorpay_order_id
    payment = db.query(Payment).filter(
        Payment.order_id == order.id,
        Payment.razorpay_order_id == razorpay_order_id
    ).order_by(Payment.created_at.desc()).first()
    
    # If not found, get the latest payment for this order (fallback)
    if not payment:
        payment = db.query(Payment).filter(
            Payment.order_id == order.id
        ).order_by(Payment.created_at.desc()).first()
        
        if not payment:
            # Create payment record if it doesn't exist (shouldn't happen, but handle gracefully)
            logger.warning(f"Payment record not found for order {order.order_number}. Creating new payment record.")
            payment = Payment(
                order_id=order.id,
                payment_method=PaymentMethod.RAZORPAY,
                payment_status=PaymentStatus.PENDING,
                razorpay_order_id=razorpay_order_id,
                amount=order.total_amount,
                currency="INR",
                notes=f"Payment record created during webhook confirmation (Razorpay order ID: {razorpay_order_id})"
            )
            db.add(payment)
            db.flush()
        else:
            # Update existing payment with razorpay_order_id if it's missing or different
            if not payment.razorpay_order_id or payment.razorpay_order_id != razorpay_order_id:
                payment.razorpay_order_id = razorpay_order_id
                db.flush()
    
    # Validate razorpay_order_id matches (after potential update)
    if payment.razorpay_order_id != razorpay_order_id:
        raise ValueError(f"Razorpay order ID mismatch. Expected {payment.razorpay_order_id}, got {razorpay_order_id}")
    
    # IDEMPOTENCY CHECK: If already confirmed, return early (idempotent)
    # This protects against late failure events and ensures idempotency
    if order.order_status == OrderStatus.CONFIRMED:
        logger.info(f"Order {order.order_number} already confirmed. Webhook event ignored (idempotent).")
        return order
    
    # Update payment status atomically
    previous_payment_status = payment.payment_status
    previous_order_status = order.order_status
    
    # Update payment record
    payment.razorpay_payment_id = razorpay_payment_id
    payment.payment_status = PaymentStatus.COMPLETED
    payment.payment_date = now_ist()
    payment.updated_at = now_ist()
    
    # Update payment method details if provided
    if payment_method_details:
        payment.payment_method_details = payment_method_details
    if payment_method_metadata:
        payment.payment_method_metadata = payment_method_metadata
    
    # Create payment transition
    payment_transition = PaymentTransition(
        payment_id=payment.id,
        from_status=previous_payment_status,
        to_status=PaymentStatus.COMPLETED,
        transition_reason="Payment confirmed by webhook (payment.captured event)",
        triggered_by="system",
        webhook_log_id=webhook_log_id
    )
    db.add(payment_transition)
    
    # Update order (denormalized payment_status)
    order.payment_status = PaymentStatus.COMPLETED
    order.order_status = OrderStatus.CONFIRMED
    order.status_updated_at = now_ist()
    
    # Update all order items - sync with order status
    item_previous_statuses = {}
    for item in order.items:
        item_previous_statuses[item.id] = item.order_status
    
    # Sync all order item statuses with order status
    sync_order_item_statuses_with_order_status(db, order, OrderStatus.CONFIRMED, update_timestamp=True)
    
    # Create status history for order confirmation
    status_history = OrderStatusHistory(
        order_id=order.id,
        status=OrderStatus.CONFIRMED,
        previous_status=previous_order_status,
        notes=f"Order confirmed by webhook (payment.captured). Payment verified. Previous status: {previous_order_status.value}",
        changed_by="system"
    )
    db.add(status_history)
    
    # Create status history for each order item
    for item in order.items:
        previous_item_status = item_previous_statuses.get(item.id, OrderStatus.PENDING_PAYMENT)
        item_status_history = OrderStatusHistory(
            order_id=order.id,
            order_item_id=item.id,
            status=OrderStatus.CONFIRMED,
            previous_status=previous_item_status,
            notes="Order item confirmed by webhook. Payment verified.",
            changed_by="system"
        )
        db.add(item_status_history)
    
    db.flush()
    
    # Clear cart items (idempotent - clear all cart items for user to ensure nothing is missed)
    try:
        from Cart_module.Cart_model import CartItem, Cart
        
        # Get user's active cart
        cart = db.query(Cart).filter(
            Cart.user_id == order.user_id,
            Cart.is_active == True
        ).first()
        
        # Get cart items first to check if user has any
        cart_items = db.query(CartItem).filter(
            CartItem.user_id == order.user_id,
            CartItem.is_deleted == False
        ).all()
        
        # If cart doesn't exist but user has items, create cart
        if not cart and cart_items:
            # Get all active carts for this user
            active_carts = db.query(Cart).filter(
                Cart.user_id == order.user_id,
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
                    user_id=order.user_id,
                    is_active=True
                )
                db.add(cart)
                db.flush()
                db.refresh(cart)
            
            # Update items to use the new cart_id
            for item in cart_items:
                if not item.cart_id:
                    item.cart_id = cart.id
        
        # Soft delete all cart items for the user's cart to ensure nothing is missed
        # This is safer than matching specific items which might miss items added after order creation
        if cart and cart_items:
            # Re-query with cart_id to ensure we get all items
            cart_items = db.query(CartItem).filter(
                CartItem.cart_id == cart.id,
                CartItem.is_deleted == False
            ).all()
        
        deleted_count = len(cart_items)
        for item in cart_items:
            item.is_deleted = True
        
        # Update cart's last_activity_at
        if cart:
            cart.last_activity_at = now_ist()
        
        # Remove applied coupon after successful payment
        from Cart_module.coupon_service import remove_coupon_from_cart, record_coupon_usage

        # Record permanent coupon usage BEFORE removing from cart
        if order.coupon_code:
            record_coupon_usage(
                db=db,
                coupon_code=order.coupon_code,
                user_id=order.user_id,
                order_id=order.id,
                order_number=order.order_number,
                discount_amount=order.coupon_discount or 0.0
            )
            # No explicit commit here — db.flush() in record_coupon_usage stages the row,
            # and the final db.commit() at the end of confirm_order_from_webhook commits it.

        remove_coupon_from_cart(db, order.user_id)
        
        logger.info(f"Cart cleared for user {order.user_id} after order {order.order_number} confirmation. {deleted_count} item(s) removed.")
    except Exception as e:
        # Log error but don't fail order confirmation if cart clearing fails
        # However, this is a critical operation, so log as error for monitoring
        logger.error(
            f"CRITICAL: Error clearing cart for order {order.order_number} (user {order.user_id}): {str(e)}. "
            f"Order confirmed but cart may still contain items. Manual intervention may be required.",
            exc_info=True
        )
    
    # Track genetic test participants after order confirmation
    try:
        from GeneticTest_module.GeneticTest_crud import create_or_update_participant
        
        for order_item in order.items:
            if order_item.member_id and order_item.product_id:
                member = db.query(Member).filter(
                    Member.id == order_item.member_id,
                    Member.is_deleted == False
                ).first()
                if not member:
                    logger.warning(f"Member {order_item.member_id} not found or deleted for participant tracking")
                    continue
                
                product = db.query(Product).filter(
                    Product.ProductId == order_item.product_id,
                    Product.is_deleted == False
                ).first()
                if not product:
                    logger.warning(f"Product {order_item.product_id} not found or deleted for participant tracking")
                    continue
                
                plan_type = None
                if product.plan_type:
                    plan_type = product.plan_type.value if hasattr(product.plan_type, 'value') else str(product.plan_type)
                
                create_or_update_participant(
                    db=db,
                    user_id=order.user_id,
                    member_id=order_item.member_id,
                    mobile=member.mobile,
                    name=member.name,
                    plan_type=plan_type,
                    product_id=order_item.product_id,
                    category_id=product.category_id,
                    order_id=order.id,
                    has_taken_genetic_test=True
                )
                logger.info(f"Tracked genetic test participant: member {order_item.member_id}, plan_type: {plan_type}")
    except Exception as e:
        # Log error but don't fail order confirmation
        # However, this is a critical operation, so log as error for monitoring
        logger.error(
            f"CRITICAL: Error tracking genetic test participants for order {order.order_number} (ID: {order.id}): {str(e)}. "
            f"Order confirmed but genetic test flag may not be set. Manual intervention may be required.",
            exc_info=True
        )
    
    # Create Razorpay invoice for the confirmed order (idempotent with respect to this function)
    try:
        from .razorpay_service import create_razorpay_customer, create_razorpay_invoice_for_order

        # Try to reuse an existing customer_id from any previous order of this user
        existing_with_customer = (
            db.query(Order)
            .filter(
                Order.user_id == order.user_id,
                Order.razorpay_customer_id.isnot(None)
            )
            .order_by(Order.created_at.desc())
            .first()
        )

        customer_id = existing_with_customer.razorpay_customer_id if existing_with_customer else None

        user = order.user

        # Create a new Razorpay customer if we don't have one yet
        if not customer_id:
            # Build contact number from user's mobile (decrypt and normalize to last 10 digits if possible)
            contact = None
            try:
                from Login_module.Utils.phone_encryption import decrypt_phone

                raw_mobile = getattr(user, "mobile", None)
                if raw_mobile:
                    try:
                        decrypted_mobile = decrypt_phone(raw_mobile)
                    except Exception:
                        # If decryption fails, fall back to raw value
                        decrypted_mobile = raw_mobile

                    digits_only = "".join(filter(str.isdigit, str(decrypted_mobile)))
                    if len(digits_only) >= 10:
                        contact = digits_only[-10:]
                    elif len(digits_only) > 0:
                        contact = digits_only
            except Exception:
                # If anything goes wrong while preparing contact, keep it None
                contact = None

            customer = create_razorpay_customer(
                name=getattr(user, "name", None),
                email=getattr(user, "email", None),
                contact=contact,
            )
            customer_id = customer.get("id")

        if not customer_id:
            logger.error(
                f"Unable to determine Razorpay customer_id for order {order.order_number} (user {order.user_id}). "
                f"Invoice will not be created."
            )
        else:
            # Persist customer_id immediately so retry-invoice endpoint can work
            # even if invoice creation fails below
            order.razorpay_customer_id = customer_id
            db.flush()

            # Log invoice creation attempt
            logger.info(f"Creating Razorpay invoice for order {order.order_number} with customer_id={customer_id}, amount={order.total_amount}")

            invoice = create_razorpay_invoice_for_order(
                customer_id=customer_id,
                order_number=order.order_number,
                total_amount=order.total_amount,
                currency=payment.currency if payment and payment.currency else "INR",
                email=getattr(user, "email", None)
            )

            # Validate invoice response before persisting
            if not invoice:
                raise ValueError(f"Invoice creation returned None for order {order.order_number}")

            if not invoice.get("id"):
                raise ValueError(f"Invoice missing 'id' field. Response keys: {list(invoice.keys())}")

            # Persist invoice details on the order for frontend and future email sending
            order.razorpay_customer_id = customer_id
            order.razorpay_invoice_id = invoice.get("id")
            order.razorpay_invoice_number = invoice.get("invoice_number") or invoice.get("number")
            order.razorpay_invoice_url = invoice.get("short_url") or invoice.get("invoice_url") or invoice.get("pdf_url")
            order.razorpay_invoice_status = invoice.get("status")

            logger.info(
                f"Razorpay invoice created for order {order.order_number} (ID: {order.id}) - "
                f"invoice_id={order.razorpay_invoice_id}, status={order.razorpay_invoice_status}"
            )
    except Exception as e:
        # Log error but don't fail order confirmation if invoice creation fails
        logger.error(
            f"CRITICAL: Error creating Razorpay invoice for order {order.order_number} (ID: {order.id}): {str(e)}. "
            f"Order confirmed but invoice may not be created. Manual intervention may be required.",
            exc_info=True
        )

    # ── CUSTOM PDF INVOICE GENERATION & EMAIL SENDING ─────
    try:
        import sys
        import os
        from pathlib import Path
        
        # Ensure the 'invoice generation' folder is in the system path for imports
        # We use absolute path to be safe
        project_root = Path(__file__).parent.parent
        invoice_gen_path = project_root / "invoice generation"
        if str(invoice_gen_path) not in sys.path:
            sys.path.append(str(invoice_gen_path))
            
        from nucleotide_invoice_sender_wo_file import generate_and_send_invoice, build_email_body
        
        # Prepare invoice data mapping
        # We use the primary address from the order
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
            # Accumulate unit_price across all members in the group (handles blood test multi-member)
            _groups[group_key]["amount"] += item.unit_price if item.unit_price else 0
        invoice_items = list(_groups.values())
            
        # Prepare payment info
        payment_info = []
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
        # Note: GST, detailed_items, and cheque info are omitted as requested
        invoice_data = {
            "invoice_number": order.razorpay_invoice_id or order.razorpay_invoice_number or f"INV-{order.order_number}",
            "invoice_date": now_ist().strftime("%B %d, %Y"),
            "order_number": order.order_number,
            "sac_code": settings.INVOICE_SAC_CODE,
            
            "company_name": settings.INVOICE_COMPANY_NAME,
            "company_address": settings.INVOICE_COMPANY_ADDRESS,
            "pan_number": settings.INVOICE_PAN_NUMBER,
            
            "customer_name": (order.user.name if order.user else None) or "Valued Customer",
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
            logger.warning(f"Invoice logo not found at {logo_path}. Falling back to text logo.")
            logo_path = None
            
        # Send the invoice email
        invoice_bcc = [e.strip() for e in settings.INVOICE_BCC_EMAILS.split(",") if e.strip()] or None
        generate_and_send_invoice(
            invoice_data=invoice_data,
            logo_path=logo_path,
            to=order.user.email,
            subject=f"Order Confirmation & Invoice – {order.order_number}",
            body=plain_body,
            html_body=html_body,
            pdf_filename=f"Invoice-{order.order_number}.pdf",
            service_account_file=str(project_root / settings.INVOICE_SERVICE_ACCOUNT_PATH),
            sender_email=settings.INVOICE_SENDER_EMAIL,
            bcc=invoice_bcc
        )
        logger.info(f"Custom branded PDF invoice sent to {order.user.email} for order {order.order_number}")

    except Exception as e:
        # Crucial: We do not let invoice errors stop the order confirmation
        logger.error(
            f"Error in Custom PDF Invoice generation/sending for order {order.order_number}: {str(e)}",
            exc_info=True
        )

    # ── ORDER CONFIRMATION HTML EMAIL ──────────────────────────────────────
    try:
        import sys as _sys
        from pathlib import Path as _Path

        _project_root = _Path(__file__).parent.parent
        _inv_gen = str(_project_root / "invoice generation")
        if _inv_gen not in _sys.path:
            _sys.path.append(_inv_gen)

        from order_confirmation_email import send_order_confirmation_email

        # Build product names from order items (mirrors invoice_items logic above)
        _conf_groups: dict = {}
        for _item in order.items:
            _item_name = "Genetic Test Product"
            if _item.snapshot and _item.snapshot.product_data:
                _item_name = _item.snapshot.product_data.get("Name", _item_name)
            elif _item.product:
                _item_name = _item.product.Name
            _gkey = None
            if _item.snapshot and _item.snapshot.cart_item_data:
                _gkey = _item.snapshot.cart_item_data.get("group_id")
            _gkey = _gkey or str(_item.id)
            _conf_groups.setdefault(_gkey, {"name": _item_name})
        _conf_items = list(_conf_groups.values())

        send_order_confirmation_email(
            to=order.user.email,
            customer_name=(order.user.name if order.user else None) or "Valued Customer",
            items=_conf_items,
            service_account_file=str(_project_root / settings.INVOICE_SERVICE_ACCOUNT_PATH),
            sender_email=settings.INVOICE_SENDER_EMAIL,
            gif_url=settings.ORDER_CONFIRMATION_GIF_URL,
        )
        logger.info(f"Order confirmation HTML email sent to {order.user.email} for order {order.order_number}")
    except Exception as e:
        logger.error(
            f"Error sending order confirmation email for order {order.order_number}: {str(e)}",
            exc_info=True
        )

    db.commit()
    db.refresh(order)
    
    _send_order_notification(
        db, order,
        "Order confirmed",
        f"Your order {order.order_number} has been confirmed.",
        type="success"
    )
    logger.info(f"Order {order.order_number} confirmed by webhook. Payment status: COMPLETED, Order status: CONFIRMED")
    
    return order


# Legacy function - kept for backward compatibility, but deprecated
def verify_and_complete_payment(
    db: Session,
    order_id: int,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str
) -> Order:
    """
    DEPRECATED: Legacy function. Use verify_payment_frontend() instead.
    This function is kept for backward compatibility but should not be used for new code.
    """
    logger.warning("verify_and_complete_payment is deprecated. Use verify_payment_frontend() instead.")
    return verify_payment_frontend(db, order_id, razorpay_order_id, razorpay_payment_id, razorpay_signature)


def mark_payment_failed_or_cancelled(
    db: Session,
    order_id: int,
    payment_status: PaymentStatus,
    reason: str = None,
    payment_method_details: Optional[str] = None,
    payment_method_metadata: Optional[Dict[str, Any]] = None
) -> Order:
    """
    Mark payment as failed.
    Called from webhooks when payment fails.
    
    Args:
        db: Database session
        order_id: Order ID
        payment_status: PaymentStatus.FAILED
        reason: Optional reason for failure
    
    Returns:
        Updated order
    """
    if payment_status != PaymentStatus.FAILED:
        raise ValueError(f"Invalid payment status. Must be FAILED, got {payment_status}")
    
    # Load order with items to ensure all items are in session for updates
    from sqlalchemy.orm import joinedload
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    
    # Don't update if payment is already completed (protected state)
    if order.payment_status == PaymentStatus.COMPLETED:
        logger.warning(f"Cannot mark payment as {payment_status.value} for order {order.order_number} - payment already completed")
        return order
    
    # Idempotency: if order is already PAYMENT_FAILED, skip update and notification (duplicate webhook)
    if order.order_status == OrderStatus.PAYMENT_FAILED:
        logger.info(f"Order {order.order_number} already marked as payment failed (idempotent).")
        return order
    
    # Get the latest payment for this order
    payment = db.query(Payment).filter(
        Payment.order_id == order.id
    ).order_by(Payment.created_at.desc()).first()
    
    if not payment:
        logger.warning(f"Payment record not found for order {order.order_number}. Creating new payment record.")
        # Create a payment record if it doesn't exist (shouldn't happen, but handle gracefully)
        payment = Payment(
            order_id=order.id,
            payment_method=PaymentMethod.RAZORPAY,
            payment_status=PaymentStatus.FAILED,
            razorpay_order_id="",  # Unknown
            amount=order.total_amount,
            currency="INR",
            notes=f"Payment record created during failure marking. Reason: {reason or 'Unknown'}"
        )
        db.add(payment)
        db.flush()
    
    # Update payment status
    previous_payment_status = payment.payment_status
    payment.payment_status = PaymentStatus.FAILED
    payment.updated_at = now_ist()
    
    # Update payment method details if provided
    if payment_method_details:
        payment.payment_method_details = payment_method_details
    if payment_method_metadata:
        payment.payment_method_metadata = payment_method_metadata
    
    if reason:
        payment.notes = (payment.notes or "") + f" | Failed: {reason}"
    
    # Create payment transition
    payment_transition = PaymentTransition(
        payment_id=payment.id,
        from_status=previous_payment_status,
        to_status=PaymentStatus.FAILED,
        transition_reason=reason or "Payment failed or cancelled",
        triggered_by="system"
    )
    db.add(payment_transition)
    
    # Update order (denormalized payment_status)
    order.payment_status = PaymentStatus.FAILED
    order.status_updated_at = now_ist()
    
    # Set order_status to PAYMENT_FAILED
    previous_order_status = order.order_status
    order.order_status = OrderStatus.PAYMENT_FAILED
    item_previous_statuses = {}
    
    # Update item statuses - sync with order status
    for item in order.items:
        item_previous_statuses[item.id] = item.order_status
    
    # Sync all order item statuses with order status
    sync_order_item_statuses_with_order_status(db, order, OrderStatus.PAYMENT_FAILED, update_timestamp=True)
    
    # Create status history entry
    status_notes = f"Payment marked as FAILED."
    if reason:
        status_notes += f" Reason: {reason}"
    else:
        status_notes += " Payment verification failed or payment was declined. Payment status set to FAILED. Order status set to PAYMENT_FAILED."
    
    status_history = OrderStatusHistory(
        order_id=order.id,
        status=OrderStatus.PAYMENT_FAILED,
        previous_status=previous_order_status,
        notes=status_notes,
        changed_by="system"
    )
    db.add(status_history)
    
    # Create status history for order items
    for item in order.items:
        item_previous_status = item_previous_statuses.get(item.id, OrderStatus.PENDING_PAYMENT)
        item_status_history = OrderStatusHistory(
            order_id=order.id,
            order_item_id=item.id,
            status=OrderStatus.PAYMENT_FAILED,
            previous_status=item_previous_status,
            notes="Payment FAILED. Order item payment failed.",
            changed_by="system"
        )
        db.add(item_status_history)
    
    db.flush()
    db.refresh(order)
    
    # Notification is sent by the router after commit to avoid race (see plan: FCM only after commit).
    logger.info(f"Payment marked as {payment_status.value} for order {order.order_number} (ID: {order.id}). Previous status: {previous_payment_status.value}")
    
    return order


def update_order_status(
    db: Session,
    order_id: int,
    new_status: OrderStatus,
    changed_by: str,
    notes: Optional[str] = None,
    order_item_id: Optional[int] = None,
    address_id: Optional[int] = None,
    scheduled_date: Optional[datetime] = None,
    technician_name: Optional[str] = None,
    technician_contact: Optional[str] = None
) -> Tuple[Order, Optional[Tuple[str, str, str]]]:
    """
    Update order status and create status history entry.
    If order_item_id or address_id is provided, updates only that specific item.
    Otherwise, updates the entire order and all items with matching addresses.
    Returns (order, notification_payload) where notification_payload is (title, message, type) or None;
    caller should send notification after commit when payload is not None.
    
    Technician and scheduling fields are optional and only applied when provided.
    For statuses like 'report_ready', 'testing_in_progress', 'sample_received_by_lab',
    technician details are not needed and will be cleared if not provided.
    
    Statuses that typically need technician info:
    - scheduled: requires scheduled_date, technician_name, technician_contact
    - schedule_confirmed_by_lab: may need technician info
    - sample_collected: may need technician info
    
    Statuses that don't need technician info:
    - confirmed, sample_received_by_lab, testing_in_progress, report_ready
    """
    notif_payload: Optional[Tuple[str, str, str]] = None
    # Statuses where technician details are not relevant
    # For these statuses, if technician fields are not provided, they will be cleared
    # However, if explicitly provided, they will still be set (for flexibility)
    STATUSES_WITHOUT_TECHNICIAN = {
        OrderStatus.PENDING_PAYMENT,
        OrderStatus.PROCESSING,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.CONFIRMED,
        OrderStatus.SAMPLE_RECEIVED_BY_LAB,
        OrderStatus.TESTING_IN_PROGRESS,
        OrderStatus.REPORT_READY
    }
    
    # If status doesn't need technician info and fields are not provided, clear them
    # If fields ARE provided, use them regardless of status (for flexibility)
    should_clear_technician = new_status in STATUSES_WITHOUT_TECHNICIAN
    
    # Load order with items to ensure all items are in session for updates
    from sqlalchemy.orm import joinedload
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    
    # Validate payment status for post-payment statuses
    # Post-payment statuses can only be set if payment is completed
    POST_PAYMENT_STATUSES = {
        OrderStatus.SCHEDULED,
        OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
        OrderStatus.SAMPLE_COLLECTED,
        OrderStatus.SAMPLE_RECEIVED_BY_LAB,
        OrderStatus.TESTING_IN_PROGRESS,
        OrderStatus.REPORT_READY
    }
    
    if new_status in POST_PAYMENT_STATUSES:
        if order.payment_status != PaymentStatus.COMPLETED:
            raise ValueError(
                f"Cannot set order status to {new_status.value} because payment status is {order.payment_status.value}. "
                f"Payment must be completed before setting post-payment statuses."
            )
    
    # Prevent setting order back to PENDING_PAYMENT if payment is already completed
    if new_status == OrderStatus.PENDING_PAYMENT and order.payment_status == PaymentStatus.COMPLETED:
        raise ValueError(
            "Cannot set order status to PENDING_PAYMENT because payment is already completed. "
            "Order status should remain CONFIRMED or progress to next stages."
        )
    
    
    # Prevent manually setting order to CONFIRMED - only webhook can confirm orders
    # This ensures cart clearing and genetic test flag setting happen correctly
    if new_status == OrderStatus.CONFIRMED:
        raise ValueError(
            "Cannot manually set order status to CONFIRMED. "
            "Order confirmation must happen via webhook (confirm_order_from_webhook) to ensure "
            "cart clearing and genetic test flag setting occur correctly."
        )
    
    # If updating specific order item
    if order_item_id:
        order_item = db.query(OrderItem).filter(
            OrderItem.id == order_item_id,
            OrderItem.order_id == order_id
        ).first()
        if not order_item:
            raise ValueError("Order item not found")
        
        previous_status = order_item.order_status
        order_item.order_status = new_status
        order_item.status_updated_at = now_ist()
        
        # Update technician and scheduling fields only if provided
        # For statuses that don't need technician info, clear fields if not provided
        if scheduled_date is not None:
            order_item.scheduled_date = scheduled_date
        elif should_clear_technician:
            order_item.scheduled_date = None
            
        if technician_name is not None:
            order_item.technician_name = technician_name
        elif should_clear_technician:
            order_item.technician_name = None
            
        if technician_contact is not None:
            order_item.technician_contact = technician_contact
        elif should_clear_technician:
            order_item.technician_contact = None
        
        # Create status history entry for this item
        status_history = OrderStatusHistory(
            order_id=order_id,
            order_item_id=order_item_id,
            status=new_status,
            previous_status=previous_status,
            notes=notes or f"Status updated for order item {order_item_id}",
            changed_by=changed_by
        )
        db.add(status_history)
        
        # Also update order-level status if all items have the same status
        notif_payload = _sync_order_status(db, order)
    
    # If updating by address_id (all items with that address)
    elif address_id:
        order_items = db.query(OrderItem).filter(
            OrderItem.order_id == order_id,
            OrderItem.address_id == address_id
        ).all()
        
        if not order_items:
            raise ValueError(f"No order items found for address {address_id} in this order")
        
        for item in order_items:
            previous_status = item.order_status
            item.order_status = new_status
            item.status_updated_at = now_ist()
            
            # Update technician and scheduling fields only if provided
            # For statuses that don't need technician info, clear fields if not provided
            if scheduled_date is not None:
                item.scheduled_date = scheduled_date
            elif should_clear_technician:
                item.scheduled_date = None
                
            if technician_name is not None:
                item.technician_name = technician_name
            elif should_clear_technician:
                item.technician_name = None
                
            if technician_contact is not None:
                item.technician_contact = technician_contact
            elif should_clear_technician:
                item.technician_contact = None
            
            # Create status history entry for each item
            status_history = OrderStatusHistory(
                order_id=order_id,
                order_item_id=item.id,
                status=new_status,
                previous_status=previous_status,
                notes=notes or f"Status updated for address {address_id}",
                changed_by=changed_by
            )
            db.add(status_history)
        
        # Also update order-level status if all items have the same status
        notif_payload = _sync_order_status(db, order)
    
    # Update entire order (default behavior)
    else:
        previous_status = order.order_status
        order.order_status = new_status
        order.status_updated_at = now_ist()

        # Capture each item's previous status before sync overwrites it
        item_previous_statuses = {item.id: item.order_status for item in order.items}

        # Update all order items to match - sync with order status
        sync_order_item_statuses_with_order_status(db, order, new_status, update_timestamp=True)
        notif_payload = _get_notification_payload_for_status(order, new_status)

        # Update technician and scheduling fields for all items only if provided
        # For statuses that don't need technician info, clear fields if not provided
        for item in order.items:
            if scheduled_date is not None:
                item.scheduled_date = scheduled_date
            elif should_clear_technician:
                item.scheduled_date = None

            if technician_name is not None:
                item.technician_name = technician_name
            elif should_clear_technician:
                item.technician_name = None

            if technician_contact is not None:
                item.technician_contact = technician_contact
            elif should_clear_technician:
                item.technician_contact = None

        # Create status history entry for order (order-level, order_item_id=None)
        status_history = OrderStatusHistory(
            order_id=order_id,
            status=new_status,
            previous_status=previous_status,
            notes=notes,
            changed_by=changed_by
        )
        db.add(status_history)

        # Create status history entry for each order item so item-level tracking shows these transitions
        for item in order.items:
            item_previous = item_previous_statuses.get(item.id, item.order_status)
            item_status_history = OrderStatusHistory(
                order_id=order_id,
                order_item_id=item.id,
                status=new_status,
                previous_status=item_previous,
                notes=notes or f"Order status updated to {new_status.value}",
                changed_by=changed_by
            )
            db.add(item_status_history)
    
    db.commit()
    db.refresh(order)
    
    return (order, notif_payload)


def _get_notification_payload_for_status(order: Order, target_status: OrderStatus) -> Optional[Tuple[str, str, str]]:
    """Return (title, message, type) for order status notification, or None if we don't notify for this status."""
    order_num = order.order_number
    messages = {
        OrderStatus.COMPLETED: ("Report ready", f"Your lab report for order {order_num} is ready.", "success"),
        OrderStatus.SCHEDULED: ("Sample scheduled", f"Your sample collection for order {order_num} has been scheduled.", "info"),
        OrderStatus.SCHEDULE_CONFIRMED_BY_LAB: ("Schedule confirmed", f"Lab has confirmed the schedule for order {order_num}.", "info"),
        OrderStatus.SAMPLE_COLLECTED: ("Sample collected", f"Sample for order {order_num} has been collected.", "info"),
        OrderStatus.SAMPLE_RECEIVED_BY_LAB: ("Sample at lab", f"Lab has received the sample for order {order_num}.", "info"),
        OrderStatus.TESTING_IN_PROGRESS: ("Testing in progress", f"Testing in progress for order {order_num}.", "info"),
        OrderStatus.REPORT_READY: ("Report ready", f"Your lab report for order {order_num} is ready.", "success"),
    }
    if target_status in messages:
        return messages[target_status]
    return None


def _sync_order_status(db: Session, order: Order) -> Optional[Tuple[str, str, str]]:
    """
    Sync order-level status based on order items.
    If all items have the same status, update order status to match.
    Otherwise, keep order status as the most common status or leave as is.
    Returns notification payload (title, message, type) if order status changed to a notifiable status, else None.
    """
    if not order.items:
        return None
    
    # Get all item statuses
    item_statuses = [item.order_status for item in order.items]
    
    # Special case: If all items have reached REPORT_READY, mark order as COMPLETED
    if len(set(item_statuses)) == 1 and item_statuses[0] == OrderStatus.REPORT_READY:
        target_status = OrderStatus.COMPLETED
    # Determine target status
    elif len(set(item_statuses)) == 1:
        target_status = item_statuses[0]
    else:
        from collections import Counter
        target_status = Counter(item_statuses).most_common(1)[0][0]
    
    # Validate status change is consistent with payment status
    # Don't set to PENDING_PAYMENT if payment is completed
    if target_status == OrderStatus.PENDING_PAYMENT and order.payment_status == PaymentStatus.COMPLETED:
        # If payment is completed, don't sync to PENDING_PAYMENT
        # Keep current order status or use CONFIRMED as minimum
        if order.order_status != OrderStatus.PENDING_PAYMENT:
            return None  # Keep current status
        target_status = OrderStatus.CONFIRMED
    
    # Don't set to COMPLETED if payment is not completed
    if target_status == OrderStatus.COMPLETED and order.payment_status != PaymentStatus.COMPLETED:
        # If payment is not completed, cannot mark order as COMPLETED
        # Keep current status or use REPORT_READY if all items are REPORT_READY
        if all(status == OrderStatus.REPORT_READY for status in item_statuses):
            target_status = OrderStatus.REPORT_READY
        else:
            return None  # Keep current status
    
    # Don't set to CONFIRMED or post-payment statuses if payment is not completed
    POST_PAYMENT_STATUSES = {
        OrderStatus.CONFIRMED,
        OrderStatus.SCHEDULED,
        OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
        OrderStatus.SAMPLE_COLLECTED,
        OrderStatus.SAMPLE_RECEIVED_BY_LAB,
        OrderStatus.TESTING_IN_PROGRESS,
        OrderStatus.REPORT_READY
    }
    
    if target_status in POST_PAYMENT_STATUSES and order.payment_status != PaymentStatus.COMPLETED:
        # If payment is not completed, don't sync to post-payment statuses
        # Keep current status or use appropriate status based on payment
        if order.order_status not in POST_PAYMENT_STATUSES:
            return None  # Keep current status
        # If payment failed, keep as PENDING_PAYMENT (user can retry), otherwise keep current status
        if order.payment_status == PaymentStatus.FAILED:
            target_status = OrderStatus.PENDING_PAYMENT
        else:
            # Keep current status or use PENDING_PAYMENT
            target_status = order.order_status if order.order_status else OrderStatus.PENDING_PAYMENT
    
    # Only update if status actually changed
    if order.order_status != target_status:
        previous_order_status = order.order_status
        order.order_status = target_status
        order.status_updated_at = now_ist()
        
        # Create status history entry if order was auto-marked as COMPLETED
        # (when all items reached REPORT_READY)
        if target_status == OrderStatus.COMPLETED and previous_order_status != OrderStatus.COMPLETED:
            # Check if all items are REPORT_READY (the condition that triggered COMPLETED)
            if all(item.order_status == OrderStatus.REPORT_READY for item in order.items):
                status_history = OrderStatusHistory(
                    order_id=order.id,
                    status=OrderStatus.COMPLETED,
                    previous_status=previous_order_status,
                    notes="Order automatically marked as COMPLETED - all order items have reached REPORT_READY stage.",
                    changed_by="system"
                )
                db.add(status_history)
        # Return payload for router to send after commit (no _notify here)
        return _get_notification_payload_for_status(order, target_status)
    return None


def get_order_by_id(db: Session, order_id: int, user_id: Optional[int] = None, include_all_payment_statuses: bool = False) -> Optional[Order]:
    """
    Get order by ID, optionally filtered by user access.
    By default, only returns orders with verified payment for security.
    Set include_all_payment_statuses=True to allow viewing pending/failed orders (for order owner only).
    
    User can access order if:
    1. Order belongs to user (Order.user_id = user_id), OR
    2. User has transferred members that appear in the order (via order_items)
    
    Args:
        db: Database session
        order_id: Order ID
        user_id: Optional user ID for access control
        include_all_payment_statuses: If True, allows viewing orders with any payment status (for order owner)
    """
    from Member_module.Member_model import Member
    
    # Build query - allow all payment statuses if flag is set
    query = db.query(Order).filter(Order.id == order_id)
    
    # If not including all statuses, filter to completed only
    if not include_all_payment_statuses:
        query = query.filter(Order.payment_status == PaymentStatus.COMPLETED)
    
    if user_id:
        # Check if user owns the order OR has transferred members in the order
        order = query.first()
        if not order:
            return None
        
        # Check if user owns the order
        if order.user_id == user_id:
            return order
        
        # User doesn't have access (removed transferred member check)
        
        # User doesn't have access
        return None
    
    return query.first()


def get_order_by_number(db: Session, order_number: str, user_id: Optional[int] = None, include_all_payment_statuses: bool = False) -> Optional[Order]:
    """
    Get order by order number, optionally filtered by user access.
    By default, only returns orders with verified payment for security.
    Set include_all_payment_statuses=True to allow viewing pending/failed orders (for order owner only).
    
    User can access order if:
    1. Order belongs to user (Order.user_id = user_id), OR
    2. User has transferred members that appear in the order (via order_items)
    
    Args:
        db: Database session
        order_number: Order number (e.g., "ORD2024012012345678")
        user_id: Optional user ID for access control
        include_all_payment_statuses: If True, allows viewing orders with any payment status (for order owner)
    """
    from Member_module.Member_model import Member
    
    # Build query - allow all payment statuses if flag is set
    query = db.query(Order).filter(Order.order_number == order_number)
    
    # If not including all statuses, filter to completed only
    if not include_all_payment_statuses:
        query = query.filter(Order.payment_status == PaymentStatus.COMPLETED)
    
    if user_id:
        # Check if user owns the order OR has transferred members in the order
        order = query.first()
        if not order:
            return None
        
        # Check if user owns the order
        if order.user_id == user_id:
            return order
        
        # User doesn't have access (removed transferred member check)
        
        # User doesn't have access
        return None
    
    return query.first()


def get_user_orders(db: Session, user_id: int, limit: int = 50, payment_status_filter: Optional[PaymentStatus] = None) -> List[Order]:
    """
    Get all orders for a user.
    By default, returns all orders regardless of payment status so users can see pending/failed orders.
    Can optionally filter by payment_status if needed.
    
    Returns orders owned by the user (Order.user_id = user_id)
    
    Args:
        db: Database session
        user_id: User ID
        limit: Maximum number of orders to return
        payment_status_filter: Optional payment status filter (None = show all statuses)
    """
    from Member_module.Member_model import Member
    
    # Build filter for user's own orders
    own_orders_filter = [Order.user_id == user_id]
    if payment_status_filter:
        own_orders_filter.append(Order.payment_status == payment_status_filter)
    
    # Get user's own orders - show all payment statuses by default
    own_orders = db.query(Order).filter(*own_orders_filter).all()
    
    # Sort by created_at descending and limit
    all_orders = sorted(
        own_orders,
        key=lambda x: x.created_at,
        reverse=True
    )[:limit]
    
    return all_orders


# ------------------------------------------------------------------ #
# Deferred order creation helpers
# ------------------------------------------------------------------ #

def create_pending_checkout(
    db: Session,
    user_id: int,
    razorpay_order_id: str,
    amount: float,
    cart_item_ids: List[int],
    address_id: Optional[int],
    placed_by_member_id: Optional[int] = None,
    coupon_code: Optional[str] = None,
    coupon_discount: float = 0.0,
) -> "PendingCheckout":
    """
    Create a PendingCheckout record when the user initiates payment.
    No Order row is created here — no order number is allocated.
    The Order is created only after payment is confirmed.
    """
    from .pending_checkout_model import PendingCheckout
    pc = PendingCheckout(
        user_id=user_id,
        razorpay_order_id=razorpay_order_id,
        amount=amount,
        cart_item_ids=cart_item_ids,
        address_id=address_id,
        placed_by_member_id=placed_by_member_id,
        coupon_code=coupon_code,
        coupon_discount=coupon_discount,
    )
    db.add(pc)
    db.commit()
    db.refresh(pc)
    return pc


def create_order_post_payment(
    db: Session,
    pending_checkout_id: int,
    razorpay_payment_id: str,
    razorpay_order_id: str,
    payment_method_details: Optional[str] = None,
    payment_method_metadata: Optional[dict] = None,
    webhook_log_id: Optional[int] = None,
) -> Order:
    """
    Create the Order (and allocate the sequential order number) ONLY after
    payment is confirmed — called from both verify-payment and the webhook handler.

    Idempotent: if the PendingCheckout already has an order_id set another
    call simply returns the already-created Order.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from .pending_checkout_model import PendingCheckout
    from Cart_module.Cart_model import CartItem as _CartItem, ProductType as _PT

    # Lock the PendingCheckout row to prevent a race between verify-payment and webhook
    pc_stmt = (
        select(PendingCheckout)
        .where(PendingCheckout.id == pending_checkout_id)
        .with_for_update()
    )
    pc = db.execute(pc_stmt).scalars().first()
    if not pc:
        raise ValueError(f"PendingCheckout {pending_checkout_id} not found")

    # Idempotent: order already created (other path won the race)
    if pc.order_id:
        order_stmt = (
            select(Order)
            .options(joinedload(Order.items))
            .where(Order.id == pc.order_id)
        )
        return db.execute(order_stmt).scalars().unique().first()

    # Create the Order now — this is the ONLY place order numbers are allocated
    order = create_order_from_cart(
        db=db,
        user_id=pc.user_id,
        address_id=pc.address_id,
        cart_item_ids=pc.cart_item_ids,
        razorpay_order_id=razorpay_order_id,
        placed_by_member_id=pc.placed_by_member_id,
        prevalidated_coupon_code=pc.coupon_code,
        prevalidated_coupon_discount=pc.coupon_discount,
    )
    # create_order_from_cart commits internally — order + payment row now exist

    # Link PendingCheckout → Order so subsequent calls are idempotent
    pc.order_id = order.id
    db.flush()

    # Immediately confirm the order (payment is already captured)
    order = confirm_order_from_webhook(
        db=db,
        order_id=order.id,
        razorpay_order_id=razorpay_order_id,
        razorpay_payment_id=razorpay_payment_id,
        webhook_log_id=webhook_log_id,
        payment_method_details=payment_method_details,
        payment_method_metadata=payment_method_metadata,
    )

    # Book Thyrocare orders for any blood test items (non-blocking).
    # IMPORTANT: PendingCheckout flow previously confirmed payment but skipped booking entirely,
    # which meant no Thyrocare API call was made and no failed-bookings were recorded.
    try:
        from Thyrocare_module.thyrocare_booking_service import book_thyrocare_for_order

        book_thyrocare_for_order(db, order)
    except Exception as thyrocare_err:
        # Do NOT fail post-payment order creation; booking can be retried via
        # POST /thyrocare/orders/retry-booking/{order_id}
        logger.error(
            f"Thyrocare booking failed for order {getattr(order, 'order_number', None) or order.id}: {thyrocare_err}"
        )

    db.commit()
    db.refresh(order)
    return order

