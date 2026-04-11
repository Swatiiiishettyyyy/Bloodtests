"""
Order model - stores order information and payment details.
No COD option, no refund policy.
All timestamps stored in IST (Indian Standard Time).
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Enum, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist
import enum


class OrderStatus(str, enum.Enum):
    """Order tracking statuses"""
    CART = "CART"  # Items in cart, no order created yet (conceptual state)
    PENDING = "PENDING"  # Pending status for order items
    PENDING_PAYMENT = "PENDING_PAYMENT"  # Order created, waiting for payment
    PROCESSING = "PROCESSING"  # Frontend verified, waiting for webhook
    PAYMENT_FAILED = "PAYMENT_FAILED"  # Payment failed - order not confirmed
    CONFIRMED = "CONFIRMED"  # Payment verified by webhook, order finalized
    COMPLETED = "COMPLETED"  # Order completed
    SCHEDULED = "SCHEDULED"
    SCHEDULE_CONFIRMED_BY_LAB = "SCHEDULE_CONFIRMED_BY_LAB"
    SAMPLE_COLLECTED = "SAMPLE_COLLECTED"
    SAMPLE_RECEIVED_BY_LAB = "SAMPLE_RECEIVED_BY_LAB"
    TESTING_IN_PROGRESS = "TESTING_IN_PROGRESS"
    REPORT_READY = "REPORT_READY"


class PaymentStatus(str, enum.Enum):
    """Payment status - tracks money/transaction state"""
    NONE = "NONE"  # No order exists (null/N/A state)
    PENDING = "PENDING"  # Order created, payment not started
    PROCESSING = "PROCESSING"  # Frontend verified, waiting for webhook
    FAILED = "FAILED"  # Payment failed
    COMPLETED = "COMPLETED"  # Webhook confirmed payment


class PaymentMethod(str, enum.Enum):
    """Payment methods (No COD)"""
    RAZORPAY = "RAZORPAY"
    # Add other online payment methods as needed


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, nullable=False, index=True)  # Unique order number
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Member profile that was active when order was placed (for viewing/filtering purposes)
    placed_by_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True)
    # Primary address reference (we use snapshot for order details, but keep FK for reference)
    address_id = Column(Integer, ForeignKey("addresses.id", ondelete="RESTRICT"), nullable=True, index=True)
    
    # Order totals
    subtotal = Column(Float, nullable=False)
    delivery_charge = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)
    coupon_code = Column(String(50), nullable=True, index=True)  # Applied coupon code (None if no coupon)
    coupon_discount = Column(Float, default=0.0)  # Discount from coupon
    total_amount = Column(Float, nullable=False)  # Final amount paid
    
    # Payment status (denormalized for quick queries - actual payment data is in payments table)
    payment_status = Column(Enum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING, index=True)
    
    # Order status tracking
    order_status = Column(Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING_PAYMENT, index=True)
    status_updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)
    
    # Additional notes
    notes = Column(Text, nullable=True)
    
    # Razorpay customer and invoice details (set after successful payment + invoice creation)
    razorpay_customer_id = Column(String(255), nullable=True, index=True)
    razorpay_invoice_id = Column(String(255), nullable=True, index=True)
    razorpay_invoice_number = Column(String(255), nullable=True)
    razorpay_invoice_url = Column(String(500), nullable=True)
    razorpay_invoice_status = Column(String(50), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    # Thyrocare booking reference (set after successful Thyrocare booking post-payment)
    thyrocare_order_id = Column(String(100), nullable=True, index=True)
    thyrocare_booking_status = Column(String(20), nullable=True, index=True)  # BOOKED / FAILED

    # Relationships
    user = relationship("User")
    address = relationship("Address")
    placed_by_member = relationship("Member", foreign_keys=[placed_by_member_id])


class OrderItem(Base):
    """
    Order items - links orders to products and members.
    Each order item represents one product purchased for one member.
    Each item has its own status for tracking per-address delivery.
    """
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)  # User who placed the order
    # These foreign keys reference entities (we use OrderSnapshot for data integrity)
    # RESTRICT prevents deletion if referenced, ensuring IDs never become NULL
    product_id = Column(Integer, ForeignKey("products.ProductId", ondelete="RESTRICT"), nullable=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="RESTRICT"), nullable=True, index=True)
    address_id = Column(Integer, ForeignKey("addresses.id", ondelete="RESTRICT"), nullable=True)
    
    # Product snapshot at time of order (from snapshot table)
    snapshot_id = Column(Integer, ForeignKey("order_snapshots.id"), nullable=False)
    
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, nullable=False)  # SpecialPrice at time of order (final price per unit)

    # Blood test: Thyrocare order reference (set after Thyrocare booking post-payment)
    thyrocare_order_id = Column(String(100), nullable=True, index=True)
    thyrocare_product_id = Column(Integer, ForeignKey("thyrocare_products.id", ondelete="RESTRICT"), nullable=True)
    thyrocare_booking_status = Column(String(20), nullable=True, index=True)  # PENDING, BOOKED, FAILED
    thyrocare_booking_error = Column(String(500), nullable=True)  # Error message if booking failed
    
    # Per-item status tracking (for different addresses in couple/family packs)
    order_status = Column(Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING_PAYMENT, index=True)
    status_updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)
    
    # Technician and scheduling information (per item, since items can have different addresses)
    scheduled_date = Column(DateTime(timezone=True), nullable=True)  # When technician will visit for this item
    technician_name = Column(String(100), nullable=True)  # Technician assigned to this item
    technician_contact = Column(String(20), nullable=True)  # Technician contact for this item
    
    created_at = Column(DateTime(timezone=True), default=now_ist)
    
    # Relationships
    order = relationship("Order", backref="items")
    user = relationship("User")
    product = relationship("Product")
    member = relationship("Member")
    address = relationship("Address")
    snapshot = relationship("OrderSnapshot")


class OrderSnapshot(Base):
    """
    Order snapshot - captures cart state at time of order.
    Stores product details, member details, address details as they were when order was placed.
    """
    __tablename__ = "order_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)  # User who placed the order
    
    # Snapshot of product details at order time
    product_data = Column(JSON, nullable=False)  # {ProductId, Name, Price, SpecialPrice, plan_type, category, etc.}
    
    # Snapshot of member details at order time
    member_data = Column(JSON, nullable=False)  # {id, name, relation, age, gender, mobile}
    
    # Snapshot of address details at order time
    address_data = Column(JSON, nullable=False)  # {id, address_label, street_address, city, state, postal_code, etc.}
    
    # Snapshot of cart item details (not required, can be empty)
    cart_item_data = Column(JSON, nullable=True)  # Original cart item data if needed (optional)
    
    created_at = Column(DateTime(timezone=True), default=now_ist)
    
    # Relationships
    order = relationship("Order", backref="snapshots")
    user = relationship("User")


class OrderStatusHistory(Base):
    """
    Order status history - tracks all status changes for an order.
    """
    __tablename__ = "order_status_history"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id", ondelete="CASCADE"), nullable=True, index=True)  # NULL for order-level status
    status = Column(Enum(OrderStatus), nullable=False, index=True)
    previous_status = Column(Enum(OrderStatus), nullable=True)  # NULL for initial status
    notes = Column(Text, nullable=False)  # Additional notes about status change
    changed_by = Column(String(100), nullable=False)  # Who changed the status (user_id or "system")
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    
    # Relationships
    order = relationship("Order", backref="status_history")
    order_item = relationship("OrderItem", backref="status_history")


class Payment(Base):
    """
    Payment table - stores payment information separately from orders.
    One order can have multiple payment attempts (for retries).
    """
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Payment method
    payment_method = Column(Enum(PaymentMethod), nullable=False, default=PaymentMethod.RAZORPAY)
    
    # Payment method details (extracted from Razorpay webhook)
    # Examples: "upi", "netbanking", "wallet", "card", "emi", etc.
    payment_method_details = Column(String(100), nullable=True, index=True)  # e.g., "upi", "netbanking", "wallet", "card"
    payment_method_metadata = Column(JSON, nullable=True)  # Additional details like VPA, bank name, wallet name, card details, etc.
    
    # Payment status
    payment_status = Column(Enum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING, index=True)
    
    # Razorpay details
    razorpay_order_id = Column(String(255), nullable=False, index=True)  # Razorpay order ID (not unique - can have multiple attempts)
    razorpay_payment_id = Column(String(255), nullable=True, unique=True, index=True)  # Razorpay payment ID (unique per successful payment)
    razorpay_signature = Column(String(255), nullable=True)  # Razorpay signature for verification
    
    # Payment amount and currency
    amount = Column(Float, nullable=False)  # Amount paid
    currency = Column(String(10), nullable=False, default="INR")
    
    # Payment date
    payment_date = Column(DateTime(timezone=True), nullable=True)  # Payment completion date
    
    # Additional notes
    notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)
    
    # Relationships
    order = relationship("Order", backref="payments")


class WebhookLog(Base):
    """
    Webhook log table - stores all webhook events from Razorpay.
    Used for debugging, auditing, and reprocessing failed webhooks.
    """
    __tablename__ = "webhook_logs"

    id = Column(Integer, primary_key=True, index=True)
    
    # Webhook event details
    event_type = Column(String(100), nullable=False, index=True)  # e.g., "payment.captured", "payment.failed"
    event_id = Column(String(255), nullable=True, unique=True, index=True)  # Razorpay event ID (unique)
    
    # Webhook payload (stored as JSON)
    payload = Column(JSON, nullable=False)  # Full webhook payload
    
    # Processing status
    processed = Column(Boolean, default=False, nullable=False, index=True)  # Whether webhook was processed successfully
    processing_error = Column(Text, nullable=True)  # Error message if processing failed
    
    # Signature verification
    signature_valid = Column(Boolean, nullable=True)  # Whether signature was valid
    signature_verification_error = Column(Text, nullable=True)  # Error if signature verification failed
    
    # Related order/payment IDs (extracted from payload)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="SET NULL"), nullable=True, index=True)
    razorpay_order_id = Column(String(255), nullable=True, index=True)
    razorpay_payment_id = Column(String(255), nullable=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)  # When webhook was processed
    
    # Relationships
    order = relationship("Order", backref="webhook_logs")
    payment = relationship("Payment", backref="webhook_logs")


class PaymentTransition(Base):
    """
    Payment transition table - tracks all payment status changes.
    Provides audit trail for payment status transitions.
    """
    __tablename__ = "payment_transitions"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Status transition
    from_status = Column(Enum(PaymentStatus), nullable=True)  # Previous status (NULL for initial status)
    to_status = Column(Enum(PaymentStatus), nullable=False, index=True)  # New status
    
    # Transition details
    transition_reason = Column(Text, nullable=True)  # Reason for transition (e.g., "webhook confirmed", "frontend verified")
    triggered_by = Column(String(100), nullable=False, default="system")  # Who triggered the transition (user_id or "system")
    
    # Additional context
    razorpay_event_id = Column(String(255), nullable=True)  # Razorpay event ID if triggered by webhook
    webhook_log_id = Column(Integer, ForeignKey("webhook_logs.id", ondelete="SET NULL"), nullable=True)  # Related webhook log
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    
    # Relationships
    payment = relationship("Payment", backref="transitions")
    webhook_log = relationship("WebhookLog", backref="payment_transitions")


