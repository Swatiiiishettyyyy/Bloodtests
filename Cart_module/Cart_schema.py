from pydantic import BaseModel, Field, validator
from typing import Optional, List, Union, Dict
from datetime import datetime, date

class MemberAddressMapping(BaseModel):
    """Mapping of member to address"""
    member_id: int = Field(..., description="Member ID", gt=0)
    address_id: int = Field(..., description="Address ID for this member", gt=0)


class GeneticSlotSet(BaseModel):
    """
    Set (or update) the appointment slot for a single genetic test cart item.
    Each member in a genetic group gets their own slot independently.
    The time range (e.g. 9–10 am) is represented by start + end time strings.
    """
    appointment_date: date = Field(..., description="Collection date (YYYY-MM-DD)")
    appointment_start_time: str = Field(..., description="Slot start time, e.g. '09:00'")
    appointment_end_time: str = Field(..., description="Slot end time, e.g. '10:00'")

    @validator("appointment_start_time", "appointment_end_time")
    def valid_time_format(cls, v):
        import re
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("Time must be in HH:MM format, e.g. '09:00'")
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("Invalid time value")
        return v


class GeneticSlotClear(BaseModel):
    """Clear the slot for one or all members in a genetic group."""
    clear_all_in_group: bool = Field(
        False,
        description="If True, clears slots for all members in the same group_id"
    )


class BloodTestMemberMapping(BaseModel):
    """Each member for a blood test shares the same address (home collection)"""
    member_id: int = Field(..., description="Member ID", gt=0)


class BloodTestCartAdd(BaseModel):
    thyrocare_product_id: int = Field(..., description="ThyrocareProduct.id to add to cart", gt=0)
    address_id: int = Field(..., description="Home collection address ID", gt=0)
    members: List[BloodTestMemberMapping] = Field(..., description="Members to test (1 to beneficiaries_max)", min_items=1)
    appointment_date: Optional[date] = Field(None, description="Preferred appointment date (YYYY-MM-DD)")
    appointment_start_time: Optional[str] = Field(None, description="Preferred time slot e.g. '09:00'")

    @validator("members")
    def no_duplicate_members(cls, v):
        ids = [m.member_id for m in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate members are not allowed.")
        return v

class CartAdd(BaseModel):
    product_id: int = Field(..., description="Product ID to add to cart", gt=0)
    member_address_map: List[MemberAddressMapping] = Field(..., description="List of member-address mappings. Each member must be explicitly mapped to an address.", min_items=1, max_items=4)
    quantity: int = Field(..., description="Quantity", ge=1)
    
    @validator('member_address_map')
    def validate_member_address_map(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one member-address mapping is required')
        if len(v) > 4:
            raise ValueError('Maximum 4 members allowed per product')
        
        # Check for duplicate member_ids
        member_ids = [mapping.member_id for mapping in v]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError('You cannot add the same family member twice for this product. Each family member can only be added once.')
        
        return v


class CartUpdate(BaseModel):
    quantity: int = Field(..., description="New quantity (must be >= 1)", ge=1)
    
    @validator('quantity')
    def validate_quantity(cls, v):
        if v < 1:
            raise ValueError('Quantity must be at least 1')
        return v


class CartItemResponse(BaseModel):
    cart_id: int
    user_id: Optional[int] = None
    product_id: int
    quantity: int
    price: float
    special_price: float
    total_amount: float
    cart_items_count: int

    class Config:
        from_attributes = True


# ---------------- NEW CART RESPONSE SCHEMAS ---------------- #

class CartItemDetail(BaseModel):
    cart_item_id: int
    product_id: int
    product_name: str
    product_image: str
    price: float
    special_price: float
    quantity: int
    total_amount: float

    class Config:
        from_attributes = True


class CartSummary(BaseModel):
    cart_id: int
    total_items: int
    subtotal_amount: float
    discount_amount: float
    coupon_amount: float = 0.0  # Discount from coupon
    coupon_code: Optional[str] = None  # Applied coupon code
    you_save: float = 0.0  # Total savings (discount + coupon)
    delivery_charge: float
    grand_total: float

    class Config:
        from_attributes = True


class ApplyCouponRequest(BaseModel):
    coupon_code: str = Field(..., description="Coupon code to apply", min_length=1, max_length=50)
    
    @validator('coupon_code')
    def validate_coupon_code(cls, v):
        if not v or not v.strip():
            raise ValueError('Coupon code is required')
        return v.strip().upper()


class CartData(BaseModel):
    cart_summary: CartSummary
    cart_items: List[CartItemDetail]

    class Config:
        from_attributes = True


class CartResponse(BaseModel):
    status: str
    message: str
    data: CartData

    class Config:
        from_attributes = True


class CouponCreate(BaseModel):
    coupon_code: str = Field(..., description="Coupon code (will be converted to uppercase)", min_length=1, max_length=50)
    description: Optional[str] = Field(None, description="Coupon description", max_length=500)
    discount_type: str = Field(..., description="Discount type: 'percentage' or 'fixed'")
    discount_value: float = Field(..., description="Discount value (percentage 0-100 or fixed amount)", gt=0)
    min_order_amount: float = Field(0.0, description="Minimum order amount to apply coupon", ge=0)
    max_discount_amount: Optional[float] = Field(None, description="Maximum discount cap (for percentage coupons)", ge=0)
    max_uses: Optional[int] = Field(None, description="Total confirmed-order uses allowed (None = unlimited)", ge=1)
    max_uses_per_user: Optional[int] = Field(1, description="Max times a single user can use this coupon (default 1, None = unlimited)", ge=1)
    valid_from: datetime = Field(..., description="Coupon valid from date (ISO format)")
    valid_until: datetime = Field(..., description="Coupon valid until date (ISO format)")
    status: str = Field("active", description="Coupon status: 'active', 'inactive', or 'expired'")
    allowed_plan_types: Optional[str] = Field(
        None,
        description="Comma-separated plan types this coupon applies to, e.g. 'individual' or 'individual,couple'. Leave empty for all plans."
    )
    
    @validator('coupon_code')
    def normalize_coupon_code(cls, v):
        return v.strip().upper()
    
    @validator('discount_type')
    def validate_discount_type(cls, v):
        if v.lower() not in ['percentage', 'fixed']:
            raise ValueError('discount_type must be "percentage" or "fixed"')
        return v.lower()
    
    @validator('discount_value')
    def validate_discount_value(cls, v, values):
        discount_type = values.get('discount_type', '').lower()
        if discount_type == 'percentage' and (v < 0 or v > 100):
            raise ValueError('Percentage discount must be between 0 and 100')
        return v
    
    @validator('status')
    def validate_status(cls, v):
        if v.lower() not in ['active', 'inactive', 'expired']:
            raise ValueError('status must be "active", "inactive", or "expired"')
        return v.lower()


class AllowlistEntry(BaseModel):
    user_id: Optional[int] = None
    mobile: Optional[str] = None

    @validator("mobile", always=True)
    def at_least_one(cls, mobile, values):
        if not values.get("user_id") and not mobile:
            raise ValueError("At least one of user_id or mobile is required")
        return mobile


class AllowlistAddRequest(BaseModel):
    coupon_code: str
    entries: List[AllowlistEntry]


class AllowlistRemoveRequest(BaseModel):
    coupon_code: str
    entries: List[AllowlistEntry]


class AllowlistEntryResponse(BaseModel):
    id: int
    coupon_id: int
    user_id: Optional[int] = None
    mobile: Optional[str] = None

    class Config:
        orm_mode = True
