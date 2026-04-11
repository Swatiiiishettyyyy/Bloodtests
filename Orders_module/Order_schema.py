"""
Order schemas for request/response models.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, date


class CreateOrderRequest(BaseModel):
    """Request to create order from cart"""
    cart_id: Optional[int] = Field(None, description="Cart ID (optional reference, all user's active cart items are included)", gt=0)


class RazorpayOrderResponse(BaseModel):
    """Response after creating order with Razorpay order details"""
    order_id: int
    order_number: str
    razorpay_order_id: str
    amount: float
    currency: str = "INR"


class VerifyPaymentRequest(BaseModel):
    """Razorpay payment verification request"""
    razorpay_order_id: str = Field(..., description="Razorpay order ID from create order response", min_length=1)
    razorpay_payment_id: str = Field(..., description="Razorpay payment ID from payment gateway", min_length=1)
    razorpay_signature: str = Field(..., description="Razorpay signature for verification", min_length=1)
    order_id: int = Field(..., description="Order ID from create order response", gt=0)


class PaymentVerificationResponse(BaseModel):
    """Payment verification response - returned for both success and failure"""
    status: str
    message: str
    order_id: int
    order_number: str
    payment_status: str
    order_status: Optional[str] = None


class UpdateOrderStatusRequest(BaseModel):
    """Request to update order status"""
    status: str = Field(..., description="New order status")
    notes: Optional[str] = Field(None, description="Notes about the status change")
    order_item_id: Optional[int] = Field(None, description="Update specific order item only (optional). If omitted, updates order-level status and all items.")
    address_id: Optional[int] = Field(None, description="Update all items with this address (optional). If omitted with order_item_id, updates order-level status and all items.")
    scheduled_date: Optional[datetime] = Field(None, description="Scheduled date for technician visit")
    technician_name: Optional[str] = Field(None, description="Technician name")
    technician_contact: Optional[str] = Field(None, description="Technician contact")


class MemberDetails(BaseModel):
    """Member details for order tracking"""
    member_id: int
    name: str
    relation: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    dob: Optional[str] = None
    mobile: Optional[str] = None


class AddressDetails(BaseModel):
    """Address details for order tracking"""
    address_id: Optional[int] = None
    address_label: Optional[str] = None
    street_address: Optional[str] = None
    landmark: Optional[str] = None
    locality: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None


class OrderItemTracking(BaseModel):
    """Order item tracking information"""
    order_item_id: int
    product_id: int
    product_name: str
    plan_type: Optional[str] = None
    group_id: Optional[str] = None
    quantity: int
    unit_price: float
    member: MemberDetails
    address: AddressDetails
    current_status: str
    previous_status: Optional[str] = None
    status_updated_at: Optional[str] = None
    scheduled_date: Optional[str] = None
    technician_name: Optional[str] = None
    technician_contact: Optional[str] = None
    created_at: Optional[str] = None
    status_history: List[Dict[str, Any]] = []


class OrderTrackingResponse(BaseModel):
    """Order tracking response"""
    order_id: int
    order_number: str
    order_date: Optional[str] = None
    payment_status: str
    current_status: str
    subtotal: float
    product_discount: Optional[float] = None
    coupon_code: Optional[str] = None
    coupon_discount: float
    delivery_charge: float
    total_amount: float
    status_updated_at: Optional[str] = None
    payment_confirmed_at: Optional[str] = None
    payment_failed_at: Optional[str] = None
    order_items: List[OrderItemTracking]


class OrderItemTrackingResponse(BaseModel):
    """Order item tracking response"""
    order_id: int
    order_number: str
    order_item_id: int
    current_status: str
    status_updated_at: Optional[str] = None
    status_history: List[Dict[str, Any]]
    member: Dict[str, Any]
    address: Dict[str, Any]
    product: Dict[str, Any]
    quantity: int
    unit_price: float
    scheduled_date: Optional[str] = None
    technician_name: Optional[str] = None
    technician_contact: Optional[str] = None


class OrderResponse(BaseModel):
    """Order response model"""
    order_number: str
    user_id: int
    address_id: Optional[int] = None
    subtotal: float
    discount: float
    coupon_code: Optional[str] = None
    coupon_discount: float
    delivery_charge: float
    total_amount: float
    payment_status: str
    order_status: str
    payment_method: Optional[str] = None
    payment_method_details: Optional[str] = None
    payment_method_metadata: Optional[Dict[str, Any]] = None
    razorpay_order_id: Optional[str] = None
    razorpay_customer_id: Optional[str] = None
    razorpay_invoice_id: Optional[str] = None
    razorpay_invoice_number: Optional[str] = None
    razorpay_invoice_url: Optional[str] = None
    razorpay_invoice_status: Optional[str] = None
    created_at: Optional[datetime] = None
    status_updated_at: Optional[datetime] = None
    payment_confirmed_at: Optional[datetime] = None
    payment_failed_at: Optional[datetime] = None
    items: List[Dict[str, Any]]


class RazorpayWebhookPayload(BaseModel):
    """Razorpay webhook payload structure"""
    event: str = Field(..., description="Webhook event type (e.g., payment.captured, payment.failed)")
    payload: Dict[str, Any] = Field(..., description="Event payload containing payment/order details")
    created_at: Optional[int] = Field(None, description="Unix timestamp when event was created")


class WebhookResponse(BaseModel):
    """Webhook response"""
    status: str
    message: str


class MemberSchedule(BaseModel):
    """Per-member schedule details"""
    member_id: int = Field(..., description="Member ID to schedule")
    scheduled_date: date = Field(..., description="Scheduled date (YYYY-MM-DD)")
    slot: str = Field(..., description="Time slot label (e.g., MORNING or EVENING)")


class ScheduleOrderRequest(BaseModel):
    """
    Request to schedule tests for an order.

    Modes:
    - use_same_slot_for_all = true: use date + slot for all members in the order.
    - use_same_slot_for_all = false: use member_schedules for per-member dates/slots.
    """
    use_same_slot_for_all: bool = Field(
        ...,
        description="If true, applies the same date and slot to all members in the order."
    )
    # Shared scheduling fields
    scheduled_date: Optional[date] = Field(
        None,
        description="Scheduled date for all members (required when use_same_slot_for_all is true)."
    )
    slot: Optional[str] = Field(
        None,
        description="Time slot label for all members (required when use_same_slot_for_all is true)."
    )
    # Per-member scheduling fields
    member_schedules: Optional[List[MemberSchedule]] = Field(
        None,
        description="Per-member schedules (required when use_same_slot_for_all is false)."
    )
