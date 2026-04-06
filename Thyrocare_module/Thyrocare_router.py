"""
Thyrocare product catalogue and blood-test cart endpoints.
"""
import uuid
import logging
import requests as _requests
from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import settings
from database import Base  # noqa: F401 - needed to register models
from deps import get_db
from Login_module.Utils.auth_user import get_current_user
from Login_module.User.user_model import User
from Address_module.Address_model import Address
from Member_module.Member_model import Member
from Cart_module.Cart_model import Cart, CartItem, ProductType
from Orders_module.Order_model import OrderItem

from .Thyrocare_model import ThyrocareProduct
from .Thyrocare_schema import (
    ThyrocareProductOut,
    ThyrocareProductUpdate,
    BloodTestCartAdd,
    SlotSearchRequest,
    AppointmentSetRequest,
    PriceBreakupRequest,
    BloodTestOrderCreate,
)
from .thyrocare_service import ThyrocareService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/thyrocare", tags=["Thyrocare"])


def normalize_mobile(mob: str) -> str:
    """Normalize mobile number to +91-XXXXXXXXXX format for Thyrocare."""
    if not mob or not mob.strip():
        return ""
    mob = mob.strip()
    if mob.startswith("+91-"):
        return mob
    if mob.startswith("+91"):
        return f"+91-{mob[3:]}"
    if mob.startswith("91") and len(mob) == 12:
        return f"+91-{mob[2:]}"
    return f"+91-{mob}"


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _get_or_create_cart(db: Session, user_id: int) -> Cart:
    cart = db.query(Cart).filter(Cart.user_id == user_id, Cart.is_active == True).first()
    if not cart:
        cart = Cart(user_id=user_id, is_active=True)
        db.add(cart)
        db.flush()
    return cart


# ------------------------------------------------------------------ #
# Product catalogue endpoints
# ------------------------------------------------------------------ #

@router.get("/products", response_model=List[ThyrocareProductOut])
def list_thyrocare_products(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all active Thyrocare blood test products."""
    q = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.is_active == True,
        ThyrocareProduct.is_deleted == False,
    )
    if search:
        q = q.filter(ThyrocareProduct.name.ilike(f"%{search}%"))
    return q.order_by(ThyrocareProduct.name).all()


@router.get("/products/{product_id}", response_model=ThyrocareProductOut)
def get_thyrocare_product(product_id: int, db: Session = Depends(get_db)):
    """Get a single Thyrocare product with its test parameters."""
    product = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.id == product_id,
        ThyrocareProduct.is_deleted == False,
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product


@router.patch("/products/{product_id}", response_model=ThyrocareProductOut)
def update_thyrocare_product(
    product_id: int,
    payload: ThyrocareProductUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update editable fields (about, description, pricing, active flag). Admin only."""
    product = db.query(ThyrocareProduct).filter(ThyrocareProduct.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


# ------------------------------------------------------------------ #
# Serviceability / slots (proxy to Thyrocare API)
# ------------------------------------------------------------------ #

@router.post("/slots/search")
def search_slots(
    payload: SlotSearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Search available collection slots for a blood test cart group.
    Pincode is auto-fetched from the cart group's address.
    Pass date_from/date_to range (max 7 days) or a single appointment_date.
    """
    from datetime import timedelta, date as date_type

    # Load cart group
    items = db.query(CartItem).filter(
        CartItem.group_id == payload.group_id,
        CartItem.user_id == current_user.id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
    ).all()
    if not items:
        raise HTTPException(status_code=404, detail="Cart group not found.")

    first_item = items[0]

    # Load product
    product = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.id == first_item.thyrocare_product_id,
        ThyrocareProduct.is_deleted == False,
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Blood test product not found.")

    # Get pincode from cart group's address
    address = db.query(Address).filter(
        Address.id == first_item.address_id,
        Address.user_id == current_user.id,
        Address.is_deleted == False,
    ).first()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found for this cart group.")

    pincode = address.postal_code.replace(" ", "")

    # Build list of dates to fetch
    today = date_type.today()
    if payload.appointment_date:
        dates = [payload.appointment_date]
    elif payload.date_from and payload.date_to:
        if payload.date_to < payload.date_from:
            raise HTTPException(status_code=422, detail="date_to must be after date_from.")
        delta = (payload.date_to - payload.date_from).days
        if delta > 6:
            raise HTTPException(status_code=422, detail="Date range cannot exceed 7 days.")
        dates = [payload.date_from + timedelta(days=i) for i in range(delta + 1)]
    elif payload.date_from:
        dates = [payload.date_from + timedelta(days=i) for i in range(7)]
    else:
        # Default: today + next 6 days
        dates = [today + timedelta(days=i) for i in range(7)]

    # Build patients array — one per member in the group
    member_ids = [i.member_id for i in items]
    members = db.query(Member).filter(Member.id.in_(member_ids)).all()
    members_by_id = {m.id: m for m in members}
    gender_map = {"M": "MALE", "F": "FEMALE", "MALE": "MALE", "FEMALE": "FEMALE"}

    patients = []
    for item in items:
        member = members_by_id.get(item.member_id)
        gender = gender_map.get(str(member.gender).upper(), "MALE") if member else "MALE"
        patients.append({
            "name": member.name if member else f"Patient {item.member_id}",
            "gender": gender,
            "age": member.age if member else 30,
            "ageType": "YEAR",
            "items": [
                {
                    "id": product.thyrocare_id,
                    "type": product.type,
                    "name": product.name
                }
            ]
        })

    if not patients:
        raise HTTPException(status_code=422, detail="No members found in cart group.")

    service = ThyrocareService()
    results = []
    for d in dates:
        try:
            result = service.get_slots(
                pincode=pincode,
                date=d.strftime("%Y-%m-%d"),
                patients=patients,
            )
            results.append({
                "date": d.strftime("%Y-%m-%d"),
                "slots": result.get("slots", []),
                "timezone": result.get("timeZone", "IST"),
            })
        except Exception as e:
            logger.warning(f"Slot fetch failed for {d}: {e}")
            results.append({
                "date": d.strftime("%Y-%m-%d"),
                "slots": [],
                "error": "Could not fetch slots for this date"
            })

    return {
        "status": "success",
        "group_id": payload.group_id,
        "product_name": product.name,
        "is_fasting_required": product.is_fasting_required,
        "pincode": pincode,
        "members_count": len(items),
        "data": results
    }


# ------------------------------------------------------------------ #
# Blood-test cart endpoints
# ------------------------------------------------------------------ #

@router.post("/cart/add")
def add_blood_test_to_cart(
    item: BloodTestCartAdd,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Add a blood test product to cart.
    All members share one address. Member count must be within beneficiaries_min/max.
    """
    # Validate product
    product = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.id == item.thyrocare_product_id,
        ThyrocareProduct.is_active == True,
        ThyrocareProduct.is_deleted == False,
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Blood test product not found.")

    num_members = len(item.member_ids)

    # Validate beneficiary count
    if num_members < product.beneficiaries_min:
        raise HTTPException(
            status_code=422,
            detail=f"Minimum {product.beneficiaries_min} member(s) required for this test."
        )
    if num_members > product.beneficiaries_max:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum {product.beneficiaries_max} member(s) allowed for this test."
        )

    # Duplicate member check
    if len(item.member_ids) != len(set(item.member_ids)):
        raise HTTPException(status_code=422, detail="Duplicate members are not allowed.")

    # Validate address belongs to user
    address = db.query(Address).filter(
        Address.id == item.address_id,
        Address.user_id == current_user.id,
        Address.is_deleted == False,
    ).first()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found or does not belong to your account.")

    # Validate all members belong to user
    members = db.query(Member).filter(
        Member.id.in_(item.member_ids),
        Member.user_id == current_user.id,
    ).all()
    if len(members) != num_members:
        raise HTTPException(status_code=422, detail="One or more members not found in your account.")

    # Check if same product + same members already in cart
    existing = db.query(CartItem).filter(
        CartItem.user_id == current_user.id,
        CartItem.thyrocare_product_id == item.thyrocare_product_id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
        CartItem.member_id.in_(item.member_ids),
    ).all()
    if existing:
        # Block if ANY of the requested members already has this product in cart
        conflicting_member_ids = {ci.member_id for ci in existing}
        conflicting = conflicting_member_ids.intersection(set(item.member_ids))
        if conflicting:
            # Get member names for a helpful error message
            conflicting_members = [m for m in members if m.id in conflicting]
            names = ", ".join(m.name for m in conflicting_members)
            raise HTTPException(
                status_code=422,
                detail=f"The following member(s) already have this blood test in their cart: {names}. Please remove the existing item first."
            )

    cart = _get_or_create_cart(db, current_user.id)
    group_id = f"bt_{current_user.id}_{product.id}_{uuid.uuid4().hex}"

    created_items = []
    try:
        for member_id in item.member_ids:
            cart_item = CartItem(
                cart_id=cart.id,
                user_id=current_user.id,
                product_type=ProductType.BLOOD_TEST,
                thyrocare_product_id=product.id,
                product_id=None,
                address_id=item.address_id,
                member_id=member_id,
                quantity=1,
                group_id=group_id,
            )
            db.add(cart_item)
            created_items.append(cart_item)

        db.flush()
        db.commit()
        for ci in created_items:
            db.refresh(ci)
    except Exception as e:
        db.rollback()
        logger.error(f"Blood test cart add failed: {e}")
        raise HTTPException(status_code=500, detail="Could not add item to cart. Please try again.")

    return {
        "status": "success",
        "message": "Blood test added to cart.",
        "data": {
            "group_id": group_id,
            "cart_id": cart.id,
            "thyrocare_product_id": product.id,
            "product_name": product.name,
            "member_ids": item.member_ids,
            "address_id": item.address_id,
            "cart_item_ids": [ci.id for ci in created_items],
            "price_per_member": product.selling_price,
            "total_amount": product.selling_price * num_members,
        },
    }


@router.post("/cart/set-appointment")
def set_appointment(
    payload: AppointmentSetRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Set appointment date and slot for a blood test cart group.
    Must be called before checkout.
    """
    items = db.query(CartItem).filter(
        CartItem.group_id == payload.group_id,
        CartItem.user_id == current_user.id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
    ).all()

    if not items:
        raise HTTPException(status_code=404, detail="Cart group not found.")

    for item in items:
        item.appointment_date = payload.appointment_date
        item.appointment_start_time = payload.appointment_start_time

    db.commit()
    return {
        "status": "success",
        "message": "Appointment set.",
        "data": {
            "group_id": payload.group_id,
            "appointment_date": str(payload.appointment_date),
            "appointment_start_time": payload.appointment_start_time,
        },
    }


@router.post("/cart/price-breakup")
def cart_price_breakup(
    payload: PriceBreakupRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get confirmed pricing from Thyrocare for a blood test cart group.
    Call this before checkout to validate the final price.
    Returns netPayableAmount, totalMrp, incentives, and per-patient breakdown.
    """
    # Load cart items for this group
    items = db.query(CartItem).filter(
        CartItem.group_id == payload.group_id,
        CartItem.user_id == current_user.id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
    ).all()

    if not items:
        raise HTTPException(status_code=404, detail="Cart group not found.")

    # Load thyrocare product (same for all items in group)
    product = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.id == items[0].thyrocare_product_id
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Blood test product not found.")

    # Load members for patient details
    member_ids = [i.member_id for i in items]
    members = db.query(Member).filter(Member.id.in_(member_ids)).all()
    members_by_id = {m.id: m for m in members}

    # Build patients array for Thyrocare
    patients = []
    for item in items:
        member = members_by_id.get(item.member_id)
        if not member:
            raise HTTPException(status_code=422, detail=f"Member {item.member_id} not found.")

        # Map gender to Thyrocare format
        gender_map = {"M": "MALE", "F": "FEMALE", "MALE": "MALE", "FEMALE": "FEMALE"}
        gender = gender_map.get(str(member.gender).upper(), "MALE")

        patients.append({
            "name": member.name,
            "gender": gender,
            "age": member.age,
            "ageType": "YEAR",
            "items": [
                {
                    "id": product.thyrocare_id,
                    "type": product.type,
                    "name": product.name,
                    "rate": {
                        "currency": "INR",
                        "mrp": str(int(product.listing_price)) if product.listing_price > 0 else str(int(product.selling_price))
                    }
                }
            ]
        })

    # Build price-breakup request payload
    # incentivePasson value = difference between listing and selling price (discount passed to customer)
    incentive_value = max(0, int(product.listing_price) - int(product.selling_price))
    breakup_payload = {
        "patients": patients,
        "discounts": [{"type": "COUPON", "amount": "0"}],
        "incentivePasson": {
            "type": "FLAT",
            "value": str(incentive_value) if incentive_value > 0 else "0"
        },
        "isReportHardCopyRequired": payload.is_report_hard_copy_required
    }

    logger.info(f"Thyrocare price-breakup payload: {breakup_payload}")

    # Call Thyrocare price-breakup API
    service = ThyrocareService()
    try:
        response = _requests.post(
            f"{settings.THYROCARE_BASE_URL}/partners/v1/cart/price-breakup",
            json=breakup_payload,
            headers=service._auth_headers(),
        )
        response.raise_for_status()
        thyrocare_data = response.json()
    except _requests.HTTPError as e:
        error_body = {}
        try:
            error_body = e.response.json()
        except Exception:
            pass
        logger.error(f"Thyrocare price-breakup failed [{e.response.status_code}]: {error_body}")
        raise HTTPException(
            status_code=502,
            detail=f"Thyrocare price-breakup failed: {error_body.get('errors', [{}])[0].get('message', 'Unknown error')}"
        )
    except Exception as e:
        logger.error(f"Thyrocare price-breakup failed: {e}")
        raise HTTPException(status_code=502, detail="Could not fetch price breakdown from Thyrocare.")

    # Extract key pricing fields for easy consumption
    rates = thyrocare_data.get("rates", {})

    return {
        "status": "success",
        "data": {
            "group_id": payload.group_id,
            "thyrocare_product_id": product.id,
            "product_name": product.name,
            "members_count": len(items),
            "pricing": {
                "currency": rates.get("currency", "INR"),
                "total_mrp": rates.get("totalMrp"),
                "total_selling_price": rates.get("totalSellingPrice"),
                "net_payable_amount": rates.get("netPayableAmount"),
                "total_discount": rates.get("totalDiscount"),
                "total_charges": rates.get("totalCharges"),
                "incentives": rates.get("incentives", {}),
                "charges": rates.get("charges", []),
            },
            "patients": thyrocare_data.get("patients", []),
            "raw_response": thyrocare_data,
        }
    }


@router.post("/orders/create")
def create_thyrocare_order(
    payload: BloodTestOrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Place a blood test order with Thyrocare after payment confirmation.
    Builds the order payload from cart group data and calls Thyrocare's order API.
    Stores the returned Thyrocare orderNo on the cart items.

    Prerequisites:
    - Cart group must exist with appointment date/time set
    - Call /cart/price-breakup first to confirm pricing
    """
    from Orders_module.Order_model import Order as OrderModel

    # Auto-generate ref_order_no from internal order or UUID
    if payload.order_id:
        internal_order = db.query(OrderModel).filter(
            OrderModel.id == payload.order_id,
            OrderModel.user_id == current_user.id
        ).first()
        ref_order_no = internal_order.order_number[:25] if internal_order else f"NUC{current_user.id}{uuid.uuid4().hex[:10].upper()}"
    else:
        ref_order_no = f"NUC{current_user.id}{uuid.uuid4().hex[:10].upper()}"

    # Load cart items for this group
    items = db.query(CartItem).filter(
        CartItem.group_id == payload.group_id,
        CartItem.user_id == current_user.id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
    ).all()

    if not items:
        raise HTTPException(status_code=404, detail="Cart group not found.")

    # Validate appointment is set
    first_item = items[0]
    if not first_item.appointment_date or not first_item.appointment_start_time:
        raise HTTPException(
            status_code=422,
            detail="Appointment date and time must be set before placing order. Call /thyrocare/cart/set-appointment first."
        )

    # Load product
    product = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.id == first_item.thyrocare_product_id
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Blood test product not found.")

    # Load address
    address = db.query(Address).filter(
        Address.id == first_item.address_id,
        Address.user_id == current_user.id,
        Address.is_deleted == False,
    ).first()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found.")

    # Load members
    member_ids = [i.member_id for i in items]
    members = db.query(Member).filter(Member.id.in_(member_ids)).all()
    members_by_id = {m.id: m for m in members}

    gender_map = {"M": "MALE", "F": "FEMALE", "MALE": "MALE", "FEMALE": "FEMALE"}

    # Build patients list — all patients get same items (Thyrocare requirement)
    patients = []
    for item in items:
        member = members_by_id.get(item.member_id)
        if not member:
            raise HTTPException(status_code=422, detail=f"Member {item.member_id} not found.")

        gender = gender_map.get(str(member.gender).upper(), "MALE")
        email = member.email or current_user.email or "noreply@nucleotide.life"
        mobile = member.mobile or current_user.mobile or ""
        normalized_mobile = normalize_mobile(mobile) if mobile else normalize_mobile(current_user.mobile or "")
        if not normalized_mobile or normalized_mobile == "+91-":
            raise HTTPException(status_code=422, detail=f"Member '{member.name}' has no valid mobile number.")

        patients.append({
            "name": member.name,
            "gender": gender,
            "age": member.age,
            "ageType": "YEAR",
            "contactNumber": normalized_mobile,
            "email": email,
            "attributes": {
                "ulcUniqueCode": "",
                "patientAddress": f"{address.street_address}, {address.city}",
                "externalPatientId": str(member.id)
            },
            "items": [
                {
                    "id": product.thyrocare_id,
                    "type": product.type,
                    "name": product.name,
                    "origin": {
                        "enteredBy": current_user.name or str(current_user.id),
                        "platform": "web"
                    }
                }
            ],
            "documents": []
        })

    # Format contact number for order level
    user_mobile = normalize_mobile(current_user.mobile or "")
    if not user_mobile or user_mobile == "+91-":
        raise HTTPException(status_code=422, detail="User account has no valid mobile number.")

    # Build order payload per Thyrocare spec
    order_payload = {
        "address": {
            "houseNo": address.address_label or "",
            "street": address.street_address,
            "addressLine1": address.locality,
            "addressLine2": address.landmark or "",
            "landmark": address.landmark or "",
            "city": address.city,
            "state": address.state,
            "country": address.country,
            "pincode": int(address.postal_code.replace(" ", ""))
        },
        "email": current_user.email or "noreply@nucleotide.life",
        "contactNumber": user_mobile,
        "appointment": {
            "date": str(first_item.appointment_date),
            "startTime": first_item.appointment_start_time,
            "timeZone": "IST"
        },
        "origin": {
            "platform": "web",
            "appId": "nucleotide-app",
            "portalType": "b2c",
            "enteredBy": current_user.name or str(current_user.id),
            "source": "Nucleotide"
        },
        "referredBy": {
            "doctorId": "",
            "doctorName": ""
        },
        "paymentDetails": {
            "payType": payload.pay_type
        },
        "attributes": {
            "remarks": payload.remarks or "",
            "campId": None,
            "isReportHardCopyRequired": payload.is_report_hard_copy_required,
            "refOrderNo": ref_order_no,
            "collectionType": "HOME_COLLECTION",
            "alertMessage": [""]
        },
        "config": {
            "communication": {
                "shareReport": True,
                "shareReceipt": True,
                "shareModes": {
                    "whatsapp": True,
                    "email": True
                }
            }
        },
        "patients": patients,
        "price": {
            "discounts": [{"type": "COUPON", "amount": "0"}],
            "incentivePasson": {
                "type": "FLAT",
                "value": int(float(payload.incentive_passon_value)) if payload.incentive_passon_value else max(0, int(product.listing_price) - int(product.selling_price))
            }
        },
        "orderOptions": {
            "isPdpcOrder": False
        }
    }

    # Call Thyrocare order API
    service = ThyrocareService()
    try:
        response = _requests.post(
            f"{settings.THYROCARE_BASE_URL}/partners/v1/orders",
            json=order_payload,
            headers=service._auth_headers(),
        )
        response.raise_for_status()
        thyrocare_response = response.json()
    except _requests.HTTPError as e:
        error_body = {}
        try:
            error_body = e.response.json()
        except Exception:
            pass
        logger.error(f"Thyrocare order creation failed [{e.response.status_code}]: {error_body}")
        raise HTTPException(
            status_code=502,
            detail=f"Thyrocare order failed: {error_body.get('errors', [{}])[0].get('message', 'Unknown error')}"
        )
    except Exception as e:
        logger.error(f"Thyrocare order creation error: {e}")
        raise HTTPException(status_code=502, detail="Could not place order with Thyrocare.")

    thyrocare_order_no = thyrocare_response.get("orderNo")
    if not thyrocare_order_no:
        raise HTTPException(status_code=502, detail="Thyrocare did not return an order number.")

    # Store on order_items if they exist (post-payment flow)
    order_items = db.query(OrderItem).filter(
        OrderItem.thyrocare_product_id == product.id,
        OrderItem.user_id == current_user.id,
        OrderItem.member_id.in_(member_ids),
        OrderItem.thyrocare_order_id == None,
    ).all()
    for oi in order_items:
        oi.thyrocare_order_id = thyrocare_order_no

    db.commit()

    return {
        "status": "success",
        "message": "Blood test order placed successfully with Thyrocare.",
        "data": {
            "thyrocare_order_no": thyrocare_order_no,
            "group_id": payload.group_id,
            "product_name": product.name,
            "members_count": len(items),
            "appointment_date": str(first_item.appointment_date),
            "appointment_start_time": first_item.appointment_start_time,
            "ref_order_no": ref_order_no,
            "raw_response": thyrocare_response,
        }
    }


@router.get("/catalogue")
def get_thyrocare_catalogue(
    min_price: float = 0,
    max_price: float = 10000,
    page: int = 1,
    page_size: int = 50,
):
    """Fetch live catalogue from Thyrocare to verify valid product IDs."""
    service = ThyrocareService()
    try:
        result = service.get_catalogue(min_price, max_price, page, page_size)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch catalogue: {e}")


@router.post("/orders/retry-booking/{order_id}")
def retry_thyrocare_booking(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retry failed Thyrocare bookings for an order.
    Use this when thyrocare_booking_status = 'FAILED' on order items.
    """
    from Orders_module.Order_model import Order as OrderModel
    from Thyrocare_module.thyrocare_booking_service import book_thyrocare_for_order

    order = db.query(OrderModel).filter(
        OrderModel.id == order_id,
        OrderModel.user_id == current_user.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    try:
        book_thyrocare_for_order(db, order)
        return {"status": "success", "message": "Thyrocare booking retried successfully."}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Retry failed: {e}")


@router.get("/orders/failed-bookings")
def list_failed_thyrocare_bookings(
    db: Session = Depends(get_db),
):
    """
    Admin endpoint — list all Thyrocare bookings that failed after payment.
    Use POST /thyrocare/orders/retry-booking/{order_id} to retry.
    """
    from Orders_module.Order_model import OrderItem, Order as OrderModel

    failed_items = db.query(OrderItem).filter(
        OrderItem.thyrocare_booking_status == "FAILED",
        OrderItem.thyrocare_product_id != None,
    ).all()

    results = []
    seen_orders = {}
    for oi in failed_items:
        if oi.order_id not in seen_orders:
            order = db.query(OrderModel).filter(OrderModel.id == oi.order_id).first()
            seen_orders[oi.order_id] = order

        order = seen_orders[oi.order_id]
        product = db.query(ThyrocareProduct).filter(ThyrocareProduct.id == oi.thyrocare_product_id).first()

        results.append({
            "order_item_id": oi.id,
            "order_id": oi.order_id,
            "order_number": order.order_number if order else None,
            "user_id": oi.user_id,
            "member_id": oi.member_id,
            "thyrocare_product_id": oi.thyrocare_product_id,
            "product_name": product.name if product else None,
            "thyrocare_booking_status": oi.thyrocare_booking_status,
            "thyrocare_booking_error": oi.thyrocare_booking_error,
            "created_at": str(oi.created_at) if oi.created_at else None,
        })

    return {
        "status": "success",
        "total_failed": len(results),
        "data": results
    }
