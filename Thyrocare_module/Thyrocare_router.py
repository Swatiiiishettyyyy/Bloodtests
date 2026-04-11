"""
Thyrocare product catalogue and blood-test cart endpoints.
"""
import uuid
import logging
import requests as _requests
from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, Query
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

from .Thyrocare_model import ThyrocareProduct, ThyrocarePincode
from .thyrocare_webhook_model import ThyrocareOrderTracking, ThyrocarePatientTracking, ThyrocareOrderStatusHistory
from .thyrocare_lab_result_model import ThyrocareLabResult
from .Thyrocare_schema import (
    ThyrocareProductOut,
    ThyrocareProductUpdate,
    BloodTestCartAdd,
    BloodTestCartUpsert,
    ActiveCartResponse,
    ActiveCartItem,
    SlotSearchRequest,
    AppointmentSetRequest,
    PriceBreakupRequest,
    BloodTestOrderCreate,
    ThyrocareWebhookPayload,
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

@router.get("/categories")
def list_thyrocare_categories(
    db: Session = Depends(get_db),
):
    """List all distinct categories of active Thyrocare products."""
    rows = db.query(ThyrocareProduct.category).filter(
        ThyrocareProduct.is_active == True,
        ThyrocareProduct.is_deleted == False,
        ThyrocareProduct.category != None,
    ).distinct().order_by(ThyrocareProduct.category).all()

    return {"categories": [r[0] for r in rows if r[0]]}


@router.get("/products")
def list_thyrocare_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List active Thyrocare blood test products with pagination and optional category filter."""
    q = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.is_active == True,
        ThyrocareProduct.is_deleted == False,
    )
    if search:
        q = q.filter(
            ThyrocareProduct.name.ilike(f"%{search}%") |
            ThyrocareProduct.category.ilike(f"%{search}%") |
            ThyrocareProduct.short_description.ilike(f"%{search}%") |
            ThyrocareProduct.about.ilike(f"%{search}%")
        )
    if category:
        q = q.filter(ThyrocareProduct.category.ilike(f"%{category}%"))

    total = q.count()
    items = q.order_by(ThyrocareProduct.name).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
        "data": items,
    }


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


@router.get("/cart/active")
def get_active_cart(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns the active (non-deleted) cart group for the current user and product.
    Use this on page load to pre-fill previously selected members, address, and slot.
    Returns 404 if no active cart exists for this product.
    """
    items = db.query(CartItem).filter(
        CartItem.user_id == current_user.id,
        CartItem.thyrocare_product_id == product_id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
    ).order_by(CartItem.created_at.desc()).all()

    if not items:
        raise HTTPException(status_code=404, detail="No active cart found for this product.")

    # Use the most recently created group_id in case of multiple groups
    latest_group_id = items[0].group_id
    items = [ci for ci in items if ci.group_id == latest_group_id]

    product = db.query(ThyrocareProduct).filter(ThyrocareProduct.id == product_id).first()
    first = items[0]

    return {
        "status": "success",
        "data": {
            "group_id": first.group_id,
            "thyrocare_product_id": product_id,
            "product_name": product.name if product else "",
            "address_id": first.address_id,
            "member_ids": [ci.member_id for ci in items],
            "appointment_date": first.appointment_date,
            "appointment_start_time": first.appointment_start_time,
            "items": [
                {
                    "cart_item_id": ci.id,
                    "member_id": ci.member_id,
                    "appointment_date": ci.appointment_date,
                    "appointment_start_time": ci.appointment_start_time,
                }
                for ci in items
            ],
        },
    }


@router.get("/cart/active-all")
def get_all_active_carts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns all active blood test cart groups for the current user.
    Use this on checkout page load to pre-fill all products, members, addresses and slots.
    Groups are keyed by group_id, each with their product, members, address and slot state.
    """
    all_items = db.query(CartItem).filter(
        CartItem.user_id == current_user.id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
    ).order_by(CartItem.created_at.desc()).all()

    if not all_items:
        return {"status": "success", "data": {"groups": [], "total_groups": 0}}

    # Group by thyrocare_product_id — pick latest group_id per product
    product_to_latest_group: dict = {}
    for ci in all_items:
        pid = ci.thyrocare_product_id
        if pid not in product_to_latest_group:
            product_to_latest_group[pid] = ci.group_id

    # Build response per group
    groups = []
    for product_id, group_id in product_to_latest_group.items():
        group_items = [ci for ci in all_items if ci.group_id == group_id]
        product = db.query(ThyrocareProduct).filter(ThyrocareProduct.id == product_id).first()
        first = group_items[0]
        groups.append({
            "group_id": group_id,
            "thyrocare_product_id": product_id,
            "product_name": product.name if product else "",
            "address_id": first.address_id,
            "member_ids": [ci.member_id for ci in group_items],
            "appointment_date": first.appointment_date,
            "appointment_start_time": first.appointment_start_time,
            "items": [
                {
                    "cart_item_id": ci.id,
                    "member_id": ci.member_id,
                    "appointment_date": ci.appointment_date,
                    "appointment_start_time": ci.appointment_start_time,
                }
                for ci in group_items
            ],
        })

    return {
        "status": "success",
        "data": {
            "groups": groups,
            "total_groups": len(groups),
        },
    }


@router.put("/cart/upsert")
def upsert_blood_test_cart(
    item: BloodTestCartUpsert,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upsert a blood test cart group.
    - If group_id provided: soft-deletes old items for that group and creates fresh ones.
    - If no group_id: behaves like a fresh add (checks for existing conflicts first).
    Handles member/address changes cleanly without orphaned rows or 422 conflicts.
    """
    product = db.query(ThyrocareProduct).filter(
        ThyrocareProduct.id == item.thyrocare_product_id,
        ThyrocareProduct.is_active == True,
        ThyrocareProduct.is_deleted == False,
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Blood test product not found.")

    num_members = len(item.member_ids)

    if num_members < product.beneficiaries_min:
        raise HTTPException(status_code=422, detail=f"Minimum {product.beneficiaries_min} member(s) required.")
    if num_members > product.beneficiaries_max:
        raise HTTPException(status_code=422, detail=f"Maximum {product.beneficiaries_max} member(s) allowed.")
    if len(item.member_ids) != len(set(item.member_ids)):
        raise HTTPException(status_code=422, detail="Duplicate members are not allowed.")

    address = db.query(Address).filter(
        Address.id == item.address_id,
        Address.user_id == current_user.id,
        Address.is_deleted == False,
    ).first()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found or does not belong to your account.")

    members = db.query(Member).filter(
        Member.id.in_(item.member_ids),
        Member.user_id == current_user.id,
    ).all()
    if len(members) != num_members:
        raise HTTPException(status_code=422, detail="One or more members not found in your account.")

    try:
        if item.group_id:
            # Soft-delete old items for this group
            db.query(CartItem).filter(
                CartItem.group_id == item.group_id,
                CartItem.user_id == current_user.id,
                CartItem.is_deleted == False,
            ).update({"is_deleted": True})
        else:
            # No group_id — soft-delete any existing active items for this product+user
            db.query(CartItem).filter(
                CartItem.user_id == current_user.id,
                CartItem.thyrocare_product_id == item.thyrocare_product_id,
                CartItem.product_type == ProductType.BLOOD_TEST,
                CartItem.is_deleted == False,
            ).update({"is_deleted": True})

        cart = _get_or_create_cart(db, current_user.id)
        group_id = item.group_id or f"bt_{current_user.id}_{product.id}_{uuid.uuid4().hex}"

        created_items = []
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
        logger.error(f"Blood test cart upsert failed: {e}")
        raise HTTPException(status_code=500, detail="Could not update cart. Please try again.")

    return {
        "status": "success",
        "message": "Blood test cart updated.",
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
    Get confirmed pricing from Thyrocare for one or more blood test cart groups.
    Pass multiple group_ids to get a combined netPayableAmount for multi-product checkout.
    Call this before checkout to validate the final price.
    """
    gender_map = {"M": "MALE", "F": "FEMALE", "MALE": "MALE", "FEMALE": "FEMALE"}
    patients = []
    total_incentive_value = 0
    group_summaries = []

    for group_id in payload.group_ids:
        items = db.query(CartItem).filter(
            CartItem.group_id == group_id,
            CartItem.user_id == current_user.id,
            CartItem.product_type == ProductType.BLOOD_TEST,
            CartItem.is_deleted == False,
        ).all()

        if not items:
            raise HTTPException(status_code=404, detail=f"Cart group '{group_id}' not found.")

        product = db.query(ThyrocareProduct).filter(
            ThyrocareProduct.id == items[0].thyrocare_product_id
        ).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Blood test product not found for group '{group_id}'.")

        member_ids = [i.member_id for i in items]
        members = db.query(Member).filter(Member.id.in_(member_ids)).all()
        members_by_id = {m.id: m for m in members}

        for item in items:
            member = members_by_id.get(item.member_id)
            if not member:
                raise HTTPException(status_code=422, detail=f"Member {item.member_id} not found.")
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

        incentive_value = int(product.notational_incentive)
        total_incentive_value += incentive_value * len(items)
        group_summaries.append({
            "group_id": group_id,
            "thyrocare_product_id": product.id,
            "product_name": product.name,
            "members_count": len(items),
        })

    breakup_payload = {
        "patients": patients,
        "discounts": [{"type": "COUPON", "amount": "0"}],
        "incentivePasson": {
            "type": "FLAT",
            "value": str(total_incentive_value) if total_incentive_value > 0 else "0"
        },
        "isReportHardCopyRequired": payload.is_report_hard_copy_required
    }

    logger.info(f"Thyrocare price-breakup payload: {breakup_payload}")
    print(f"[DEBUG] Thyrocare price-breakup payload: {breakup_payload}")

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

    rates = thyrocare_data.get("rates", {})

    return {
        "status": "success",
        "data": {
            "group_ids": payload.group_ids,
            "groups": group_summaries,
            "total_patients": len(patients),
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
            "discounts": [{"type": "COUPON", "code": "0"}],
            "incentivePasson": {
                "type": "FLAT",
                "value": int(float(payload.incentive_passon_value)) if payload.incentive_passon_value else int(product.notational_incentive)
            }
        },
        "orderOptions": {
            "isPdpcOrder": True
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
    if payload.order_id:
        order_items = db.query(OrderItem).filter(
            OrderItem.order_id == payload.order_id,
            OrderItem.thyrocare_product_id == product.id,
            OrderItem.user_id == current_user.id,
            OrderItem.member_id.in_(member_ids),
            OrderItem.thyrocare_order_id == None,
        ).all()
    else:
        order_items = db.query(OrderItem).filter(
            OrderItem.thyrocare_product_id == product.id,
            OrderItem.user_id == current_user.id,
            OrderItem.member_id.in_(member_ids),
            OrderItem.thyrocare_order_id == None,
        ).order_by(OrderItem.created_at.desc()).limit(len(member_ids)).all()
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


@router.get("/orders/{thyrocare_order_id}/details")
def get_thyrocare_order_details(
    thyrocare_order_id: str,
    include: str = "tracking,items,price",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Fetch full order details from Thyrocare for a given Thyrocare order ID.
    Returns Thyrocare's status as-is — no internal status mapping.
    """
    from Orders_module.Order_model import OrderItem

    # Verify this thyrocare_order_id belongs to the current user
    order_item = db.query(OrderItem).filter(
        OrderItem.thyrocare_order_id == thyrocare_order_id,
        OrderItem.user_id == current_user.id,
    ).first()
    if not order_item:
        raise HTTPException(status_code=404, detail="Thyrocare order not found or does not belong to your account.")

    service = ThyrocareService()
    try:
        thyrocare_data = service.get_order_details(thyrocare_order_id, include=include)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch order details from Thyrocare: {e}")

    thyrocare_status = thyrocare_data.get("status", "")

    return {
        "status": "success",
        "data": {
            "thyrocare_order_id": thyrocare_order_id,
            "thyrocare_status": thyrocare_status,
            "order_id": order_item.order_id,
            "appointment_date": thyrocare_data.get("appointmentDate"),
            "address": thyrocare_data.get("address"),
            "phlebo": thyrocare_data.get("phlebo"),
            "payment_details": thyrocare_data.get("paymentDetails"),
            "price": thyrocare_data.get("price"),
            "patients": thyrocare_data.get("patients", []),
            "order_tracking": thyrocare_data.get("orderTracking", []),
            "alert_message": thyrocare_data.get("alertMessage"),
            "raw_response": thyrocare_data,
        }
    }


@router.get("/orders/{thyrocare_order_id}/reports/{lead_id}")
def get_thyrocare_report(
    thyrocare_order_id: str,
    lead_id: str,
    type: str = "pdf",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get diagnostic report URL for a patient from Thyrocare.
    - thyrocare_order_id: Thyrocare order ID (e.g. VL0D7027)
    - lead_id: Patient ID from order details patients[].id (e.g. SP71208745)
    - type: pdf or xml (default: pdf)
    Returns a pre-signed S3 URL valid for 10 minutes.
    """
    from Orders_module.Order_model import OrderItem

    # Verify this thyrocare_order_id belongs to the current user
    order_item = db.query(OrderItem).filter(
        OrderItem.thyrocare_order_id == thyrocare_order_id,
        OrderItem.user_id == current_user.id,
    ).first()
    if not order_item:
        raise HTTPException(status_code=404, detail="Thyrocare order not found or does not belong to your account.")

    service = ThyrocareService()
    try:
        result = service.get_report(thyrocare_order_id, lead_id, report_type=type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch report from Thyrocare: {e}")

    return {
        "status": "success",
        "data": {
            "thyrocare_order_id": thyrocare_order_id,
            "lead_id": lead_id,
            "type": type,
            "report_url": result.get("reportUrl"),
            "raw_response": result,
        }
    }


@router.get("/check-serviceable")
def check_serviceable_pincode(
    pincode: str,
    db: Session = Depends(get_db),
):
    """
    Check if a pincode is serviceable for Thyrocare blood test home collection.
    Uses locally cached thyrocare_pincodes table.
    GET /thyrocare/check-serviceable?pincode=110001
    """
    normalized = pincode.strip()
    record = db.query(ThyrocarePincode).filter(
        ThyrocarePincode.pincode == normalized,
        ThyrocarePincode.is_active == True,
    ).first()

    return {
        "pincode": normalized,
        "is_serviceable": record is not None,
    }


@router.post("/sync-pincodes")
def sync_thyrocare_pincodes(
    db: Session = Depends(get_db),
):
    """
    Admin endpoint — sync serviceable pincodes from Thyrocare into thyrocare_pincodes table.
    POST /thyrocare/sync-pincodes
    """
    from Login_module.Utils.datetime_utils import now_ist

    service = ThyrocareService()
    try:
        pincodes = service.get_serviceable_pincodes()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch pincodes from Thyrocare: {e}")

    if not pincodes:
        raise HTTPException(status_code=502, detail="Thyrocare returned empty pincode list")

    # Deactivate all existing
    db.query(ThyrocarePincode).update({"is_active": False})

    synced_at = now_ist()
    added = 0
    updated = 0

    for pc in pincodes:
        normalized = str(pc).strip()
        existing = db.query(ThyrocarePincode).filter(ThyrocarePincode.pincode == normalized).first()
        if existing:
            existing.is_active = True
            existing.synced_at = synced_at
            updated += 1
        else:
            db.add(ThyrocarePincode(pincode=normalized, is_active=True, synced_at=synced_at))
            added += 1

    db.commit()

    return {
        "status": "success",
        "total": len(pincodes),
        "added": added,
        "updated": updated,
    }


def _fetch_and_store_lab_results(thyrocare_order_id: str, patient_id: str, report_url_from_payload: str = None) -> None:
    """
    Background task — fetches XML report from Thyrocare and stores parsed results.
    Uses reportUrl from webhook payload if available, otherwise fetches from Thyrocare API.
    """
    from database import SessionLocal
    from Login_module.Utils.datetime_utils import now_ist
    import requests as _req
    import xml.etree.ElementTree as ET
    from datetime import datetime as _dt

    db = SessionLocal()
    try:
        # Use URL from webhook payload if available, otherwise fetch from Thyrocare
        if report_url_from_payload:
            report_url = report_url_from_payload
            logger.info(f"Using report URL from webhook payload for patient {patient_id}")
        else:
            service = ThyrocareService()
            report_data = service.get_report(thyrocare_order_id, patient_id, report_type="xml")
            report_url = report_data.get("reportUrl") or report_data.get("url")

        if not report_url:
            logger.warning(f"No report URL for patient {patient_id}, order {thyrocare_order_id}")
            return

        # Update report_url on patient tracking and get member_id/user_id
        patient = db.query(ThyrocarePatientTracking).filter(
            ThyrocarePatientTracking.thyrocare_order_id == thyrocare_order_id,
            ThyrocarePatientTracking.patient_id == patient_id,
        ).first()
        resolved_member_id = patient.member_id if patient else None
        resolved_user_id = patient.user_id if patient else None
        if patient:
            patient.report_url = report_url
            db.flush()

        # Fetch and parse XML
        xml_resp = _req.get(report_url, timeout=30)
        xml_resp.raise_for_status()
        root = ET.fromstring(xml_resp.content)

        lead = root if root.tag == "LEADDETAILS" else root.find(".//LEADDETAILS")
        if lead is None:
            logger.warning(f"No LEADDETAILS in XML for patient {patient_id}, order {thyrocare_order_id}")
            db.commit()
            return

        order_no = (lead.findtext("LAB_CODE") or "").strip()
        lead_id = (lead.findtext("LEADID") or patient_id).strip()

        for td in lead.findall(".//TESTDETAIL"):
            sdate_str = (td.findtext("SDATE") or "").strip()
            sdate = None
            if sdate_str:
                try:
                    sdate = _dt.fromisoformat(sdate_str)
                except Exception:
                    pass

            db.add(ThyrocareLabResult(
                thyrocare_order_id=thyrocare_order_id,
                patient_id=lead_id,
                order_no=order_no or None,
                test_code=(td.findtext("TEST_CODE") or "").strip() or None,
                description=(td.findtext("Description") or "").strip() or None,
                test_value=(td.findtext("TEST_VALUE") or "").strip() or None,
                normal_val=(td.findtext("NORMAL_VAL") or "").strip() or None,
                units=(td.findtext("UNITS") or "").strip() or None,
                indicator=(td.findtext("INDICATOR") or "").strip() or None,
                report_group=(td.findtext("REPORT_GROUP_ID") or "").strip() or None,
                sample_date=sdate,
                source="nucleotide",
                category=None,
                member_id=resolved_member_id,
                user_id=resolved_user_id,
                created_at=now_ist(),
            ))

        db.commit()
        logger.info(f"Lab results stored for patient {patient_id}, order {thyrocare_order_id}")

    except Exception as e:
        logger.error(f"Background lab result fetch failed for patient {patient_id}, order {thyrocare_order_id}: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


@router.post("/webhook")
async def thyrocare_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Thyrocare webhook endpoint — receives order status updates from Thyrocare.
    POST /thyrocare/webhook
    No authentication required (Thyrocare calls this endpoint directly).
    Must respond within 1 second.
    """
    from Login_module.Utils.datetime_utils import now_ist

    try:
        payload = await request.json()
    except Exception:
        return {"respId": "RES00001", "response": "Success"}

    thyrocare_order_id = payload.get("orderId")
    order_status = payload.get("orderStatus")
    order_status_description = payload.get("orderStatusDescription")
    thyrocare_timestamp = payload.get("timestamp")
    b2c_patient_id = payload.get("b2cPatientId") or ""
    order_data = payload.get("orderData") or {}

    if not thyrocare_order_id:
        return {"respId": "RES00001", "response": "Success"}

    try:
        # Find our internal order via thyrocare_order_id on order_items
        from Orders_module.Order_model import OrderItem, Order as OrderModel
        order_item = db.query(OrderItem).filter(
            OrderItem.thyrocare_order_id == thyrocare_order_id
        ).first()
        our_order_id = order_item.order_id if order_item else None

        # Upsert thyrocare_order_tracking
        tracking = db.query(ThyrocareOrderTracking).filter(
            ThyrocareOrderTracking.thyrocare_order_id == thyrocare_order_id
        ).first()

        phlebo = order_data.get("phlebo") or {}
        appt_date_str = order_data.get("appointmentDate")
        appt_date = None
        if appt_date_str:
            try:
                from datetime import datetime
                appt_date = datetime.fromisoformat(appt_date_str.replace("Z", "+00:00"))
            except Exception:
                pass

        if not tracking:
            tracking = ThyrocareOrderTracking(
                thyrocare_order_id=thyrocare_order_id,
                our_order_id=our_order_id,
                current_order_status=order_status,
                current_status_description=order_status_description,
                phlebo_name=phlebo.get("name"),
                phlebo_contact=phlebo.get("contactNumber"),
                appointment_date=appt_date,
                last_webhook_at=now_ist(),
                created_at=now_ist(),
            )
            db.add(tracking)
            db.flush()
        else:
            tracking.current_order_status = order_status
            tracking.current_status_description = order_status_description
            tracking.phlebo_name = phlebo.get("name") or tracking.phlebo_name
            tracking.phlebo_contact = phlebo.get("contactNumber") or tracking.phlebo_contact
            tracking.appointment_date = appt_date or tracking.appointment_date
            tracking.last_webhook_at = now_ist()
            if our_order_id and not tracking.our_order_id:
                tracking.our_order_id = our_order_id
            db.flush()

        # Append to status history (always insert)
        history = ThyrocareOrderStatusHistory(
            thyrocare_order_id=thyrocare_order_id,
            order_tracking_id=tracking.id,
            order_status=order_status,
            order_status_description=order_status_description,
            thyrocare_timestamp=thyrocare_timestamp,
            b2c_patient_id=b2c_patient_id,
            raw_payload=payload,
            received_at=now_ist(),
        )
        db.add(history)

        # Upsert patients (only SP* patient IDs)
        patients = order_data.get("patients") or []

        # Build member lookup from order_items for this thyrocare_order
        from Orders_module.Order_model import OrderItem as _OI
        from Member_module.Member_model import Member as _Member

        order_items_for_order = db.query(_OI).filter(
            _OI.thyrocare_order_id == thyrocare_order_id
        ).all()

        # Build list of (member_id, user_id, member_name) for matching — deduplicated by member_id
        member_ids_in_order = [oi.member_id for oi in order_items_for_order if oi.member_id]
        members_by_id = {}
        if member_ids_in_order:
            members = db.query(_Member).filter(_Member.id.in_(member_ids_in_order)).all()
            members_by_id = {m.id: m for m in members}

        seen_member_ids = set()
        member_lookup = []
        for oi in order_items_for_order:
            if oi.member_id and oi.member_id not in seen_member_ids:
                seen_member_ids.add(oi.member_id)
                m = members_by_id.get(oi.member_id)
                member_lookup.append({
                    "member_id": oi.member_id,
                    "user_id": oi.user_id,
                    "name": m.name.strip().upper() if m and m.name else "",
                })

        def _resolve_member(patient_name: str, index: int):
            """
            Resolve member_id and user_id for a webhook patient.
            Strategy:
            1. Name match (case-insensitive) — exact
            2. If ambiguous (multiple same name) → fall back to index
            3. If no name match → fall back to index
            """
            if not member_lookup:
                return None, None

            normalized = (patient_name or "").strip().upper()

            # Try name match
            name_matches = [m for m in member_lookup if m["name"] == normalized]

            if len(name_matches) == 1:
                return name_matches[0]["member_id"], name_matches[0]["user_id"]

            if len(name_matches) > 1:
                logger.warning(
                    f"Ambiguous name match for '{patient_name}' in order {thyrocare_order_id} "
                    f"— falling back to index {index}"
                )

            # Fall back to index
            if index < len(member_lookup):
                return member_lookup[index]["member_id"], member_lookup[index]["user_id"]

            return None, None

        sp_index = 0
        for p in patients:
            pid = p.get("id") or ""
            if not pid.upper().startswith("SP"):
                continue

            pid_index = sp_index
            sp_index += 1

            patient_name_raw = p.get("name") or ""
            resolved_member_id, resolved_user_id = _resolve_member(patient_name_raw, pid_index)

            report_ts = None
            if p.get("reportTimestamp"):
                try:
                    from datetime import datetime
                    report_ts = datetime.fromisoformat(p["reportTimestamp"].replace("Z", "+00:00"))
                except Exception:
                    pass

            existing_patient = db.query(ThyrocarePatientTracking).filter(
                ThyrocarePatientTracking.thyrocare_order_id == thyrocare_order_id,
                ThyrocarePatientTracking.patient_id == pid,
            ).first()

            if not existing_patient:
                new_patient = ThyrocarePatientTracking(
                    thyrocare_order_id=thyrocare_order_id,
                    order_tracking_id=tracking.id,
                    patient_id=pid,
                    patient_name=p.get("name"),
                    age=p.get("age"),
                    gender=p.get("gender"),
                    is_report_available=p.get("isReportAvailable", False),
                    report_url=p.get("reportUrl"),
                    report_timestamp=report_ts,
                    current_status=order_status,  # human label e.g. "Serviced"
                    member_id=resolved_member_id,
                    user_id=resolved_user_id,
                    created_at=now_ist(),
                )
                db.add(new_patient)
                db.flush()
                existing_patient = new_patient
            else:
                existing_patient.patient_name = p.get("name") or existing_patient.patient_name
                existing_patient.is_report_available = p.get("isReportAvailable", existing_patient.is_report_available)
                existing_patient.report_url = p.get("reportUrl") or existing_patient.report_url
                existing_patient.report_timestamp = report_ts or existing_patient.report_timestamp
                existing_patient.current_status = order_status  # human label
                if not existing_patient.member_id:
                    existing_patient.member_id = resolved_member_id
                if not existing_patient.user_id:
                    existing_patient.user_id = resolved_user_id

            # Auto-fetch report in background (keeps webhook response under 1 second)
            if p.get("isReportAvailable") and pid.upper().startswith("SP"):
                report_url_from_payload = p.get("reportUrl")
                background_tasks.add_task(
                    _fetch_and_store_lab_results,
                    thyrocare_order_id=thyrocare_order_id,
                    patient_id=pid,
                    report_url_from_payload=report_url_from_payload,
                )

        db.commit()
        logger.info(f"Thyrocare webhook processed: order={thyrocare_order_id} status={order_status_description}")

    except Exception as e:
        logger.error(f"Thyrocare webhook processing error: {e}", exc_info=True)
        db.rollback()

    # Always return success within 1 second
    return {"respId": "RES00001", "response": "Success"}


@router.get("/orders/my-orders")
def get_my_thyrocare_orders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all Thyrocare blood test orders for the logged-in user.
    Returns order tracking status, phlebo info, and status history.
    GET /thyrocare/orders/my-orders
    """
    orders = db.query(ThyrocareOrderTracking).filter(
        ThyrocareOrderTracking.user_id == current_user.id
    ).order_by(ThyrocareOrderTracking.created_at.desc()).all()

    if not orders:
        return {"status": "success", "total": 0, "data": []}

    order_ids = [o.id for o in orders]
    all_history = db.query(ThyrocareOrderStatusHistory).filter(
        ThyrocareOrderStatusHistory.order_tracking_id.in_(order_ids)
    ).order_by(ThyrocareOrderStatusHistory.received_at.asc()).all()

    from collections import defaultdict
    history_by_order: dict = defaultdict(list)
    for h in all_history:
        history_by_order[h.order_tracking_id].append(h)

    result = []
    for o in orders:
        result.append({
            "thyrocare_order_id": o.thyrocare_order_id,
            "our_order_id": o.our_order_id,
            "current_order_status": o.current_order_status,
            "current_status_description": o.current_status_description,
            "appointment_date": o.appointment_date.isoformat() if o.appointment_date else None,
            "phlebo_name": o.phlebo_name,
            "phlebo_contact": o.phlebo_contact,
            "last_updated": o.last_webhook_at.isoformat() if o.last_webhook_at else None,
            "member_ids": o.member_ids or [],
            "status_history": [
                {
                    "order_status": h.order_status,
                    "order_status_description": h.order_status_description,
                    "timestamp": h.thyrocare_timestamp,
                    "received_at": h.received_at.isoformat() if h.received_at else None,
                }
                for h in history_by_order.get(o.id, [])
            ],
        })

    return {"status": "success", "total": len(result), "data": result}


@router.get("/reports/my-reports")
def get_my_lab_reports(
    member_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get lab report results for the logged-in user's members.
    Optionally filter by member_id.
    GET /thyrocare/reports/my-reports?member_id=218
    """
    query = db.query(ThyrocareLabResult).filter(
        ThyrocareLabResult.user_id == current_user.id
    )

    if member_id:
        # Validate member belongs to this user
        from Member_module.Member_model import Member as _M
        member = db.query(_M).filter(
            _M.id == member_id,
            _M.user_id == current_user.id,
            _M.is_deleted == False,
        ).first()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found or does not belong to your account.")
        query = query.filter(ThyrocareLabResult.member_id == member_id)

    results = query.order_by(ThyrocareLabResult.sample_date.desc()).all()

    # Group by member_id for cleaner response
    from collections import defaultdict
    grouped: dict = defaultdict(list)
    for r in results:
        grouped[r.member_id].append({
            "thyrocare_order_id": r.thyrocare_order_id,
            "patient_id": r.patient_id,
            "order_no": r.order_no,
            "test_code": r.test_code,
            "description": r.description,
            "test_value": r.test_value,
            "normal_val": r.normal_val,
            "units": r.units,
            "indicator": r.indicator,
            "report_group": r.report_group,
            "sample_date": r.sample_date.isoformat() if r.sample_date else None,
            "category": r.category,
        })

    return {
        "status": "success",
        "total": len(results),
        "data": [
            {"member_id": mid, "results": items}
            for mid, items in grouped.items()
        ],
    }


# Thyrocare status → internal custom_status mapping
# Keys are orderStatus (human labels from webhook)
_THYROCARE_STATUS_MAP = {
    "YET TO ASSIGN": "Order Booked",
    "ASSIGNED": None,
    "STARTED": None,
    "ARRIVED": None,
    "SERVICED": "Sample Collected",
    "SAMPLE IMPORTED": "Processing",
    "RESCHEDULED": None,
    "CANCELLED": "Cancelled",
    "DONE": "Report Ready",
}


@router.get("/orders/{thyrocare_order_id}/order-details")
def get_thyrocare_order_details_combined(
    thyrocare_order_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get combined order details for a Thyrocare order.
    Combines thyrocare_order_tracking + thyrocare_order_status_history + thyrocare_patient_tracking.
    GET /thyrocare/orders/{thyrocare_order_id}/order-details
    """
    # Get order tracking
    tracking = db.query(ThyrocareOrderTracking).filter(
        ThyrocareOrderTracking.thyrocare_order_id == thyrocare_order_id,
        ThyrocareOrderTracking.user_id == current_user.id,
    ).first()

    if not tracking:
        raise HTTPException(status_code=404, detail="Order not found or does not belong to your account.")

    # Get our internal order for order_number and payment details
    our_order = None
    payment_info = None
    if tracking.our_order_id:
        from Orders_module.Order_model import Order as _Order, Payment as _Payment
        our_order = db.query(_Order).filter(_Order.id == tracking.our_order_id).first()
        if our_order:
            payment = db.query(_Payment).filter(
                _Payment.order_id == our_order.id
            ).order_by(_Payment.created_at.desc()).first()
            if payment:
                payment_info = {
                    "amount": payment.amount,
                    "currency": payment.currency,
                    "payment_status": payment.payment_status.value if hasattr(payment.payment_status, 'value') else str(payment.payment_status),
                    "payment_method": payment.payment_method_details or "RAZORPAY",
                    "razorpay_payment_id": payment.razorpay_payment_id,
                    "payment_date": payment.payment_date.isoformat() if payment.payment_date else None,
                }

    # Get status history
    history = db.query(ThyrocareOrderStatusHistory).filter(
        ThyrocareOrderStatusHistory.order_tracking_id == tracking.id
    ).order_by(ThyrocareOrderStatusHistory.received_at.asc()).all()

    # Get patients
    patients = db.query(ThyrocarePatientTracking).filter(
        ThyrocarePatientTracking.order_tracking_id == tracking.id
    ).all()

    # Map current status using human label (current_order_status)
    raw_status = tracking.current_order_status or ""
    mapped_status = _THYROCARE_STATUS_MAP.get(raw_status.upper())

    return {
        "status": "success",
        "data": {
            "order_number": our_order.order_number if our_order else None,
            "thyrocare_order_id": thyrocare_order_id,
            "current_status": mapped_status,
            "current_status_raw": raw_status,
            "appointment_date": tracking.appointment_date.isoformat() if tracking.appointment_date else None,
            "phlebo": {
                "name": tracking.phlebo_name,
                "contact": tracking.phlebo_contact,
            },
            "payment": payment_info,
            "patients": [
                {
                    "patient_id": p.patient_id,
                    "name": p.patient_name,
                    "age": p.age,
                    "gender": p.gender,
                    "member_id": p.member_id,
                    "is_report_available": p.is_report_available,
                    "report_url": p.report_url,
                    "report_timestamp": p.report_timestamp.isoformat() if p.report_timestamp else None,
                    "current_status": _THYROCARE_STATUS_MAP.get(
                        (p.current_status or "").upper()
                    ),
                }
                for p in patients
            ],
            "status_history": [
                {
                    "thyrocare_status": h.order_status,
                    "status_description": h.order_status_description,
                    "mapped_status": _THYROCARE_STATUS_MAP.get(
                        (h.order_status or "").upper()
                    ),
                    "timestamp": h.thyrocare_timestamp,
                    "received_at": h.received_at.isoformat() if h.received_at else None,
                }
                for h in history
            ],
        }
    }


@router.get("/reports/{patient_id}/download")
def download_patient_report(
    patient_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get a fresh download URL for a patient's lab report.
    Checks local DB first. If URL is expired, re-fetches from Thyrocare.
    GET /thyrocare/reports/{patient_id}/download
    """
    from fastapi.responses import RedirectResponse
    from urllib.parse import urlparse, parse_qs
    from datetime import datetime, timezone

    # Find patient record belonging to this user
    patient = db.query(ThyrocarePatientTracking).filter(
        ThyrocarePatientTracking.patient_id == patient_id,
        ThyrocarePatientTracking.user_id == current_user.id,
    ).first()

    if not patient:
        raise HTTPException(status_code=404, detail="Patient report not found or does not belong to your account.")

    if not patient.is_report_available:
        raise HTTPException(status_code=404, detail="Report is not yet available for this patient.")

    def _is_url_expired(url: str) -> bool:
        """Check if S3 pre-signed URL is expired using X-Amz-Date and X-Amz-Expires."""
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            amz_date = params.get("X-Amz-Date", [None])[0]
            amz_expires = params.get("X-Amz-Expires", [None])[0]
            if not amz_date or not amz_expires:
                return True  # Can't determine — treat as expired
            signed_at = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            expires_in = int(amz_expires)
            expiry_time = signed_at.timestamp() + expires_in
            return datetime.now(timezone.utc).timestamp() > expiry_time
        except Exception:
            return True  # On any parse error, treat as expired

    report_url = patient.report_url

    # Re-fetch if no URL or expired
    if not report_url or _is_url_expired(report_url):
        try:
            service = ThyrocareService()
            report_data = service.get_report(
                patient.thyrocare_order_id, patient_id, report_type="pdf"
            )
            fresh_url = report_data.get("reportUrl") or report_data.get("url")
            if fresh_url:
                patient.report_url = fresh_url
                db.commit()
                report_url = fresh_url
            else:
                raise HTTPException(status_code=404, detail="Could not fetch report URL from Thyrocare.")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not fetch report: {str(e)}")

    return RedirectResponse(url=report_url, status_code=302)
