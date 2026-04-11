from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional, List
import uuid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

from database import SessionLocal
from .Cart_model import CartItem, Cart
from Product_module.Product_model import Product, PlanType
from Address_module.Address_model import Address
from Member_module.Member_model import Member
from Login_module.User.user_model import User
from Thyrocare_module.Thyrocare_model import ThyrocareProduct
from .Cart_schema import CartAdd, CartUpdate, ApplyCouponRequest, CouponCreate, AllowlistAddRequest, AllowlistRemoveRequest, AllowlistEntryResponse, BloodTestCartAdd
from deps import get_db
from Login_module.Utils.auth_user import get_current_user, get_current_member
from Login_module.Utils.datetime_utils import to_ist_isoformat
from .Cart_audit_crud import create_audit_log
from .coupon_service import (
    apply_coupon_to_cart,
    get_applied_coupon,
    remove_coupon_from_cart,
    validate_and_calculate_discount,
    is_coupon_usage_limit_reached,
    get_coupon_usage_count,
    is_user_allowed_for_coupon
)
from .Coupon_model import Coupon, CouponType, CouponStatus

router = APIRouter(prefix="/cart", tags=["Cart"])


def get_client_info(request: Request):
    """Extract client IP and user agent from request"""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return ip, user_agent


def get_or_create_user_cart(db: Session, user_id: int) -> Cart:
    """
    Get or create active cart for user.
    Ensures one active cart per user by deactivating any other active carts.
    """
    # Get all active carts for this user
    active_carts = db.query(Cart).filter(
        Cart.user_id == user_id,
        Cart.is_active == True
    ).all()
    
    if len(active_carts) > 1:
        # Multiple active carts - deactivate all except the first one
        for c in active_carts[1:]:
            c.is_active = False
        cart = active_carts[0]
        db.flush()
        logger.warning(f"Found multiple active carts for user {user_id}. Deactivated {len(active_carts) - 1} cart(s).")
    elif len(active_carts) == 1:
        # One active cart exists
        cart = active_carts[0]
    else:
        # No active cart exists - create new one
        cart = Cart(
            user_id=user_id,
            is_active=True
        )
        db.add(cart)
        db.flush()
        db.refresh(cart)
        logger.info(f"Created new cart {cart.id} for user {user_id}")
    
    return cart


@router.post("/add")
def add_to_cart(
    item: CartAdd,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Add item to cart (requires authentication).
    For couple products, creates 2 rows in cart_items table.
    For family products, creates 3-4 rows (3 mandatory + 1 optional).
    Every cart item must be linked with member_id and address_id.
    Addresses can be the same for all members or different for each member.
    """
    try:
        # Check if product exists
        product = db.query(Product).filter(Product.ProductId == item.product_id, Product.is_deleted == False).first()
        if not product:
            raise HTTPException(status_code=404, detail="We couldn't find this product.")

        category_name = product.category.name if product.category else "this category"
        
        # Extract member_ids and address_ids from member_address_map
        member_ids = [mapping.member_id for mapping in item.member_address_map]
        address_ids = [mapping.address_id for mapping in item.member_address_map]
        num_members = len(member_ids)
        num_addresses = len(set(address_ids))  # Unique address count
        
        # Validate all addresses exist and belong to user
        unique_address_ids = list(set(address_ids))
        addresses = db.query(Address).filter(
            Address.id.in_(unique_address_ids),
            Address.user_id == current_user.id,
            Address.is_deleted == False
        ).all()
        
        if len(addresses) != len(unique_address_ids):
            found_ids = {addr.id for addr in addresses}
            missing_ids = [aid for aid in unique_address_ids if aid not in found_ids]
            raise HTTPException(
                status_code=422,
                detail="One or more addresses you selected are not found or don't belong to your account."
            )
        
        # Validate members exist and belong to user
        members = db.query(Member).filter(
            Member.id.in_(member_ids),
            Member.user_id == current_user.id
        ).all()
        
        if len(members) != num_members:
            raise HTTPException(
                status_code=422,
                detail="One or more family members you selected are not found in your account."
            )
        
        # Check if any of these members are already in cart for another product
        # within the same category (ignore address differences)
        conflicting_members = (
            db.query(CartItem, Product, Member)
            .join(Product, CartItem.product_id == Product.ProductId)
            .join(Member, CartItem.member_id == Member.id)
            .filter(
                Product.is_deleted == False,
                Member.is_deleted == False,
                CartItem.is_deleted == False,  # Ignore cleared (post-order) cart items
            )
            .filter(
                CartItem.user_id == current_user.id,
                CartItem.member_id.in_(member_ids),
                Product.category_id == product.category_id,
                CartItem.product_id != item.product_id
            )
            .all()
        )
        
        if conflicting_members:
            # Build raw conflicts, then deduplicate by (member_id, existing_product_id)
            raw_conflicts = []
            for cart_item, existing_product, member_obj in conflicting_members:
                raw_conflicts.append({
                    "member_id": member_obj.id,
                    "member_name": member_obj.name,
                    "existing_product_id": existing_product.ProductId,
                    "existing_product_name": existing_product.Name,
                    "existing_plan_type": existing_product.plan_type.value if hasattr(existing_product.plan_type, "value") else str(existing_product.plan_type),
                })
            seen = set()
            conflicts = []
            for c in raw_conflicts:
                key = (c["member_id"], c["existing_product_id"])
                if key not in seen:
                    seen.add(key)
                    conflicts.append(c)
            # Unique member names for message (preserve order)
            member_names = []
            for c in conflicts:
                if c["member_name"] not in member_names:
                    member_names.append(c["member_name"])
            # Product name for message: use first conflict's product; if multiple products, say "another genetic test"
            product_names = list({c["existing_product_name"] for c in conflicts})
            existing_product_name = product_names[0] if len(product_names) == 1 else "another genetic test"
            # User-friendly message: in your cart, product name, clear action
            if len(member_names) == 1:
                name = member_names[0]
                message = (
                    f"{name} is already in your cart. Please remove the existing item or select a different member."
                )
            else:
                if len(member_names) == 2:
                    members_str = f"{member_names[0]} and {member_names[1]}"
                else:
                    members_str = ", ".join(member_names[:-1]) + f" and {member_names[-1]}"
                message = (
                    f"{members_str} are already in your cart. Please remove the existing items or select different members."
                )
            client_ip, user_agent = get_client_info(request) if request else (None, None)
            logger.warning(
                f"Cart add failed - Member conflicts with existing cart items | "
                f"User ID: {current_user.id} | Product ID: {item.product_id} | "
                f"Conflicting Members: {[c['member_name'] for c in conflicts]} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=422,
                detail=message
            )
        
        # Check if same product with same members already exists in cart
        # (ignore address differences - addresses can be changed)
        # Exclude deleted items (is_deleted = True)
        existing_cart_items = db.query(CartItem).filter(
            CartItem.user_id == current_user.id,
            CartItem.product_id == item.product_id,
            CartItem.is_deleted == False  # Exclude deleted/cleared items
        ).all()
        
        if existing_cart_items:
            # Group by group_id to check member sets
            from collections import defaultdict
            grouped_existing = defaultdict(list)
            for ci in existing_cart_items:
                group_key = ci.group_id or f"single_{ci.id}"
                grouped_existing[group_key].append(ci)
            
            requested_member_ids = set(member_ids)
            for group_key, group_items in grouped_existing.items():
                existing_member_ids = set(ci.member_id for ci in group_items)
                if existing_member_ids == requested_member_ids:
                    raise HTTPException(
                        status_code=422,
                        detail="This product with the same family members is already in your cart."
                    )
        
        # Validate member count matches product plan type
        if product.plan_type == PlanType.SINGLE and num_members != 1:
            raise HTTPException(
                status_code=422,
                detail=f"Single plan requires exactly 1 family member. You selected {num_members}."
            )
        elif product.plan_type == PlanType.COUPLE and num_members != 2:
            raise HTTPException(
                status_code=422,
                detail=f"Couple plan requires exactly 2 family members. You selected {num_members}."
            )
        elif product.plan_type == PlanType.FAMILY:
            if num_members < 3 or num_members > 4:
                raise HTTPException(
                    status_code=422,
                    detail=f"Family plan requires 3 to 4 family members (3 required + 1 optional). You selected {num_members}."
                )
        
        ip, user_agent = get_client_info(request)
        
        # Generate unique group_id using full UUID for better uniqueness
        group_id = f"{current_user.id}_{product.ProductId}_{uuid.uuid4().hex}"
        
        # Get or create user's active cart
        cart = get_or_create_user_cart(db, current_user.id)
        cart_id = cart.id
        
        # Build member_address_map from request (already validated)
        member_address_map = {
            mapping.member_id: mapping.address_id 
            for mapping in item.member_address_map
        }
        
        # Create a lookup for members by ID
        members_by_id = {member.id: member for member in members}
        
        created_cart_items = []
        
        try:
            # Create cart items: one row per member with their assigned address
            # Process in the order specified in member_address_map
            for mapping in item.member_address_map:
                member = members_by_id[mapping.member_id]
                cart_item = CartItem(
                    user_id=current_user.id,
                    cart_id=cart_id,  # Use cart.id from cart table
                    product_id=item.product_id,
                    address_id=mapping.address_id,
                    member_id=mapping.member_id,
                    quantity=item.quantity,
                    group_id=group_id  # Link all items for this product purchase
                )
                db.add(cart_item)
                created_cart_items.append(cart_item)
            
            # Update cart's last_activity_at
            from Login_module.Utils.datetime_utils import now_ist
            cart.last_activity_at = now_ist()
            
            # Flush to get IDs for all items
            db.flush()
            
            # Commit all items together (transaction safety)
            db.commit()
            
            # Refresh all created items to get database values
            for cart_item in created_cart_items:
                db.refresh(cart_item)
                logger.info(f"CartItem {cart_item.id} created with cart_id: {cart_item.cart_id}")
        except Exception as e:
            # Rollback on any error to prevent partial data
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Something went wrong while adding items to your cart. Please try again."
            )
        
        # Safety check: ensure cart items were created
        if not created_cart_items:
            raise HTTPException(
                status_code=500,
                detail="No cart items were created. This should not happen."
            )
        
        # Build member-address mapping for response (preserve order from request)
        member_address_response = [
            {"member_id": mapping.member_id, "address_id": mapping.address_id}
            for mapping in item.member_address_map
        ]
        
        # Extract unique address IDs for response
        unique_address_ids = list(set(address_ids))
        
        # Audit log
        correlation_id = str(uuid.uuid4())
        create_audit_log(
            db=db,
            user_id=current_user.id,
            action="ADD",
            entity_type="CART_ITEM",
            entity_id=created_cart_items[0].id,
            cart_id=cart_id,  # Use proper cart.id
            details={
                "product_id": product.ProductId,
                "plan_type": product.plan_type.value,
                "quantity": item.quantity,
                "member_ids": member_ids,
                "address_ids": unique_address_ids,
                "member_address_map": member_address_map,
                "cart_items_created": len(created_cart_items),
                "group_id": group_id,
                "cart_id": cart_id
            },
            ip_address=ip,
            user_agent=user_agent,
            username=current_user.name or current_user.mobile,  # Pass directly to avoid lookup
            correlation_id=correlation_id
        )
        
        return {
            "status": "success",
            "message": "Product added to cart successfully.",
            "data": {
                "cart_item_ids": [ci.id for ci in created_cart_items],
                "cart_id": cart_id,  # Proper cart.id from cart table
                "product_id": product.ProductId,
                "address_ids": unique_address_ids,
                "member_ids": member_ids,
                "member_address_map": member_address_response,
                "quantity": item.quantity,
                "plan_type": product.plan_type.value,
                "price": product.Price,
                "special_price": product.SpecialPrice,
                "total_amount": item.quantity * product.SpecialPrice,
                "items_created": len(created_cart_items)  # 1 for single, 2 for couple, 3-4 for family
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="We couldn't add items to your cart. Please try again.")


@router.post("/add-blood-test")
def add_blood_test_to_cart(
    item: BloodTestCartAdd,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Add a Thyrocare blood test product to cart.
    All members share the same address (home collection).
    Creates one CartItem row per member, all linked by group_id.
    """
    try:
        from .Cart_model import ProductType as PT

        # Validate thyrocare product
        thyrocare_product = db.query(ThyrocareProduct).filter(
            ThyrocareProduct.id == item.thyrocare_product_id,
            ThyrocareProduct.is_deleted == False,
            ThyrocareProduct.is_active == True
        ).first()
        if not thyrocare_product:
            raise HTTPException(status_code=404, detail="Blood test product not found.")

        member_ids = [m.member_id for m in item.members]
        num_members = len(member_ids)

        # Validate beneficiary count
        if num_members < thyrocare_product.beneficiaries_min:
            raise HTTPException(
                status_code=422,
                detail=f"This test requires at least {thyrocare_product.beneficiaries_min} member(s). You selected {num_members}."
            )
        if num_members > thyrocare_product.beneficiaries_max:
            raise HTTPException(
                status_code=422,
                detail=f"This test allows at most {thyrocare_product.beneficiaries_max} member(s). You selected {num_members}."
            )

        # Validate address belongs to user
        address = db.query(Address).filter(
            Address.id == item.address_id,
            Address.user_id == current_user.id,
            Address.is_deleted == False
        ).first()
        if not address:
            raise HTTPException(status_code=422, detail="Address not found or doesn't belong to your account.")

        # Validate members belong to user
        members = db.query(Member).filter(
            Member.id.in_(member_ids),
            Member.user_id == current_user.id
        ).all()
        if len(members) != num_members:
            raise HTTPException(status_code=422, detail="One or more members not found in your account.")

        # Check if same thyrocare product + same members already in cart
        existing = db.query(CartItem).filter(
            CartItem.user_id == current_user.id,
            CartItem.thyrocare_product_id == item.thyrocare_product_id,
            CartItem.product_type == PT.BLOOD_TEST,
            CartItem.is_deleted == False
        ).all()
        if existing:
            from collections import defaultdict
            grouped = defaultdict(list)
            for ci in existing:
                grouped[ci.group_id].append(ci)
            requested_set = set(member_ids)
            for grp_items in grouped.values():
                if set(ci.member_id for ci in grp_items) == requested_set:
                    raise HTTPException(
                        status_code=422,
                        detail="This blood test with the same members is already in your cart."
                    )

        ip, user_agent = get_client_info(request)
        group_id = f"{current_user.id}_bt_{thyrocare_product.id}_{uuid.uuid4().hex}"
        cart = get_or_create_user_cart(db, current_user.id)

        created_items = []
        try:
            for member_id in member_ids:
                cart_item = CartItem(
                    user_id=current_user.id,
                    cart_id=cart.id,
                    product_type=PT.BLOOD_TEST,
                    thyrocare_product_id=thyrocare_product.id,
                    address_id=item.address_id,
                    member_id=member_id,
                    quantity=1,
                    group_id=group_id,
                    appointment_date=item.appointment_date,
                    appointment_start_time=item.appointment_start_time,
                )
                db.add(cart_item)
                created_items.append(cart_item)

            from Login_module.Utils.datetime_utils import now_ist
            cart.last_activity_at = now_ist()
            db.flush()
            db.commit()
            for ci in created_items:
                db.refresh(ci)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Something went wrong while adding the blood test to your cart.")

        correlation_id = str(uuid.uuid4())
        create_audit_log(
            db=db,
            user_id=current_user.id,
            action="ADD",
            entity_type="CART_ITEM",
            entity_id=created_items[0].id,
            cart_id=cart.id,
            details={
                "product_type": "blood_test",
                "thyrocare_product_id": thyrocare_product.id,
                "thyrocare_id": thyrocare_product.thyrocare_id,
                "member_ids": member_ids,
                "address_id": item.address_id,
                "group_id": group_id,
                "cart_items_created": len(created_items),
            },
            ip_address=ip,
            user_agent=user_agent,
            username=current_user.name or current_user.mobile,
            correlation_id=correlation_id
        )

        return {
            "status": "success",
            "message": "Blood test added to cart successfully.",
            "data": {
                "cart_item_ids": [ci.id for ci in created_items],
                "cart_id": cart.id,
                "thyrocare_product_id": thyrocare_product.id,
                "thyrocare_id": thyrocare_product.thyrocare_id,
                "product_name": thyrocare_product.name,
                "address_id": item.address_id,
                "member_ids": member_ids,
                "members_count": num_members,
                "price_per_member": thyrocare_product.selling_price,
                "total_amount": thyrocare_product.selling_price * num_members,
                "group_id": group_id,
                "appointment_date": str(item.appointment_date) if item.appointment_date else None,
                "appointment_start_time": item.appointment_start_time,
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="We couldn't add the blood test to your cart. Please try again.")


@router.put("/update/{cart_item_id}")
def update_cart_item(
    cart_item_id: int,
    update: CartUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update cart item quantity (requires authentication).
    For couple/family products, updates all items in the group.
    """
    
    cart_item = db.query(CartItem).filter(
        CartItem.id == cart_item_id,
        CartItem.user_id == current_user.id,
        CartItem.is_deleted == False
    ).first()
    
    if not cart_item:
        raise HTTPException(status_code=404, detail="We couldn't find this item in your cart to update.")

    old_quantity = cart_item.quantity
    group_id = cart_item.group_id
    cart_id = cart_item.cart_id
    
    try:
        # If item has a group_id, update all items in the group (for couple/family products)
        if group_id:
            # Update all items in the group (exclude deleted items)
            group_items = db.query(CartItem).filter(
                CartItem.group_id == group_id,
                CartItem.user_id == current_user.id,
                CartItem.is_deleted == False
            ).all()
            
            for item in group_items:
                item.quantity = update.quantity
            updated_count = len(group_items)
        else:
            # Single item, update just this one
            cart_item.quantity = update.quantity
            updated_count = 1
        
        # Update cart's last_activity_at
        if cart_id:
            cart = db.query(Cart).filter(
                Cart.id == cart_id,
                Cart.user_id == current_user.id  # Security: verify cart belongs to user
            ).first()
            if cart:
                from Login_module.Utils.datetime_utils import now_ist
                cart.last_activity_at = now_ist()
        
        db.commit()
        db.refresh(cart_item)

        product = cart_item.product
        
        # Audit log
        ip, user_agent = get_client_info(request)
        correlation_id = str(uuid.uuid4())
        create_audit_log(
            db=db,
            user_id=current_user.id,
            action="UPDATE",
            entity_type="CART_ITEM",
            entity_id=cart_item.id,
            cart_id=cart_id,  # Use proper cart.id
            details={
                "product_id": product.ProductId,
                "old_quantity": old_quantity,
                "new_quantity": update.quantity,
                "group_id": group_id,
                "items_updated": updated_count,
                "cart_id": cart_id
            },
            ip_address=ip,
            user_agent=user_agent,
            username=current_user.name or current_user.mobile,
            correlation_id=correlation_id
        )
        
        return {
            "status": "success",
            "message": f"Cart item(s) updated successfully. {updated_count} item(s) updated.",
            "data": {
                "cart_item_id": cart_item.id,
                "product_id": product.ProductId,
                "quantity": update.quantity,
                "price": product.Price,
                "special_price": product.SpecialPrice,
                "total_amount": update.quantity * product.SpecialPrice,
                "items_updated": updated_count
            }
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while updating your cart item. Please try again."
        )


@router.delete("/delete/{cart_item_id}")
def delete_cart_item(
    cart_item_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete cart item (requires authentication).
    For couple/family products, deletes all items in the group (all members).
    """
    
    cart_item = db.query(CartItem).filter(
        CartItem.id == cart_item_id,
        CartItem.user_id == current_user.id,
        CartItem.is_deleted == False  # Only allow deleting non-deleted items
    ).first()
    
    if not cart_item:
        raise HTTPException(status_code=404, detail="This item has already been removed from your cart.")

    product_id = cart_item.product_id
    quantity = cart_item.quantity
    group_id = cart_item.group_id
    cart_id = cart_item.cart_id
    
    # If item has a group_id, soft delete all items in the group (for couple/family products)
    if group_id:
        # Soft delete all items in the group (exclude already deleted items)
        deleted_items = db.query(CartItem).filter(
            CartItem.group_id == group_id,
            CartItem.user_id == current_user.id,
            CartItem.is_deleted == False
        ).all()
        
        deleted_count = len(deleted_items)
        for item in deleted_items:
            item.is_deleted = True
    else:
        # Single item, soft delete just this one
        cart_item.is_deleted = True
        deleted_count = 1
    
    # Remove applied coupon when cart item is deleted
    # This ensures coupon is not applied to remaining items after deletion
    coupon_removed = remove_coupon_from_cart(db, current_user.id)
    if coupon_removed:
        logger.info(f"Removed coupon from cart when deleting cart item for user {current_user.id}")
    
    # Update cart's last_activity_at
    if cart_id:
        cart = db.query(Cart).filter(
            Cart.id == cart_id,
            Cart.user_id == current_user.id  # Security: verify cart belongs to user
        ).first()
        if cart:
            from Login_module.Utils.datetime_utils import now_ist
            cart.last_activity_at = now_ist()
    
    db.commit()

    # Audit log
    ip, user_agent = get_client_info(request)
    correlation_id = str(uuid.uuid4())
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="DELETE",
        entity_type="CART_ITEM",
        entity_id=cart_item_id,
        cart_id=cart_id,  # Use proper cart.id
        details={
            "product_id": product_id,
            "quantity": quantity,
            "group_id": group_id,
            "items_deleted": deleted_count,
            "cart_id": cart_id,
            "coupon_removed": coupon_removed  # Track if coupon was removed
        },
        ip_address=ip,
        user_agent=user_agent,
        username=current_user.name or current_user.mobile,
        correlation_id=correlation_id
    )

    message = f"Cart item(s) deleted successfully. {deleted_count} item(s) removed."
    if coupon_removed:
        message += " Applied coupon has been removed."

    return {
        "status": "success",
        "message": message
    }


@router.delete("/clear")
def clear_cart(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Clear all cart items for current user (requires authentication) - soft delete"""
    
    # Get user's active cart first (or create if doesn't exist but has items)
    cart = db.query(Cart).filter(
        Cart.user_id == current_user.id,
        Cart.is_active == True
    ).first()
    
    # Get cart items first to check if user has any
    cart_items = db.query(CartItem).filter(
        CartItem.user_id == current_user.id,
        CartItem.is_deleted == False
    ).all()
    
    # If cart doesn't exist but user has items, create cart
    if not cart and cart_items:
        cart = get_or_create_user_cart(db, current_user.id)
        # Update items to use the new cart_id
        for item in cart_items:
            if not item.cart_id:
                item.cart_id = cart.id
    
    cart_id = cart.id if cart else None
    
    # Soft delete: set is_deleted = True instead of actually deleting
    # Query via cart_id if available, otherwise use all items we found
    if cart_id and cart_items:
        # Re-query with cart_id to ensure we get all items
        cart_items = db.query(CartItem).filter(
            CartItem.cart_id == cart_id,
            CartItem.is_deleted == False
        ).all()
    
    deleted_count = len(cart_items)
    for item in cart_items:
        item.is_deleted = True
    
    # Remove applied coupon when cart is cleared
    # This ensures coupon is not applied to new items added after clearing
    coupon_removed = remove_coupon_from_cart(db, current_user.id)
    if coupon_removed:
        logger.info(f"Removed coupon from cart when clearing cart for user {current_user.id}")
    
    # Update cart's last_activity_at
    if cart:
        from Login_module.Utils.datetime_utils import now_ist
        cart.last_activity_at = now_ist()
    
    db.commit()
    
    # Audit log - now with proper cart_id
    ip, user_agent = get_client_info(request)
    correlation_id = str(uuid.uuid4())
    create_audit_log(
        db=db,
        user_id=current_user.id,
        cart_id=cart_id,  # Fixed: Now includes cart_id
        action="CLEAR",
        entity_type="CART",
        details={
            "items_deleted": deleted_count,
            "cart_id": cart_id,
            "coupon_removed": coupon_removed  # Track if coupon was removed
        },
        ip_address=ip,
        user_agent=user_agent,
        username=current_user.name or current_user.mobile,
        correlation_id=correlation_id
    )
    
    message = f"Cleared {deleted_count} item(s) from the cart."
    if coupon_removed:
        message += " Applied coupon has been removed."
    
    return {
        "status": "success",
        "message": message
    }


@router.get("/view")
def view_cart(
    request: Request,
    current_user: User = Depends(get_current_user),
    current_member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """
    View cart items for current user (requires authentication).
    All cart items are visible to all members of the same user.
    The cart is the same regardless of which member profile is viewing it.
    All items for a user share the same cart_id.
    Returns cart items with product_id, address_id, cart_id, member_id.
    Groups items by group_id for couple/family products.
    """
    # Get user's active cart (or create if doesn't exist and has items)
    cart = db.query(Cart).filter(
        Cart.user_id == current_user.id,
        Cart.is_active == True
    ).first()
    
    # Get all cart items for user (don't filter by member yet - need to check plan types)
    # First try to get items via cart_id if cart exists
    if cart:
        query = db.query(CartItem).options(
            joinedload(CartItem.member),
            joinedload(CartItem.address),
            joinedload(CartItem.product),
            joinedload(CartItem.thyrocare_product)
        ).filter(
            CartItem.cart_id == cart.id,
            CartItem.is_deleted == False
        )
    else:
        query = db.query(CartItem).options(
            joinedload(CartItem.member),
            joinedload(CartItem.address),
            joinedload(CartItem.product),
            joinedload(CartItem.thyrocare_product)
        ).filter(
            CartItem.user_id == current_user.id,
            CartItem.is_deleted == False
        )
    
    all_cart_items = query.order_by(CartItem.group_id, CartItem.created_at).all()
    
    # If cart doesn't exist but user has items, create cart now
    if not cart and all_cart_items:
        cart = get_or_create_user_cart(db, current_user.id)
        # Update items to use the new cart_id
        for item in all_cart_items:
            if not item.cart_id:
                item.cart_id = cart.id
        db.flush()  # Flush instead of commit - let the endpoint commit
    
    cart_id = cart.id if cart else None
    
    # All cart items are visible to all members of the same user
    # Cart is the same regardless of which member profile is viewing it
    cart_items = all_cart_items
    
    if not cart_items:
        # Audit log
        ip, user_agent = get_client_info(request)
        create_audit_log(
            db=db,
            user_id=current_user.id,
            cart_id=cart_id,  # Include cart_id even for empty cart (may be None if no cart exists)
            action="VIEW",
            entity_type="CART",
            details={"items_count": 0, "cart_id": cart_id},
            ip_address=ip,
            user_agent=user_agent
        )
        
        return {
            "status": "success",
            "message": "Cart is empty.",
            "data": {
                "cart_summary": None,
                "cart_items": []
            }
        }

    subtotal_amount = 0
    delivery_charge = 0
    cart_item_details = []
    
    # Group items by group_id to show couple/family products together
    grouped_items = {}
    for item in cart_items:
        raw_type = getattr(item, 'product_type', None)
        is_blood_test = str(raw_type).lower() == "blood_test"
        if is_blood_test and not item.group_id:
            # Blood test items without group_id: group by product+address to keep members together
            group_key = f"bt_{item.thyrocare_product_id}_{item.address_id}"
        else:
            group_key = item.group_id or f"single_{item.id}"
        if group_key not in grouped_items:
            grouped_items[group_key] = []
        grouped_items[group_key].append(item)

    for group_key, items in grouped_items.items():
        # Skip if group is empty (should not happen, but safety check)
        if not items:
            continue

        item = items[0]
        raw_type = getattr(item, 'product_type', None)
        is_blood_test = str(raw_type).lower() == "blood_test"

        if is_blood_test:
            # ---- Blood test group ----
            thyrocare_product = item.thyrocare_product
            if not thyrocare_product:
                continue

            member_ids = [i.member_id for i in items]
            members_detail = []
            for i in items:
                member = i.member
                members_detail.append({
                    "member_id": member.id if member else i.member_id,
                    "name": member.name if member else "Unknown",
                    "relation": str(member.relation) if member else None,
                    "age": member.age if member else None,
                    "gender": member.gender if member else None,
                    "dob": to_ist_isoformat(member.dob) if member and member.dob else None,
                    "mobile": member.mobile if member else None,
                })

            address = item.address
            address_details = {
                "address_id": address.id if address else item.address_id,
                "address_label": address.address_label if address else None,
                "street_address": address.street_address if address else None,
                "landmark": address.landmark if address else None,
                "locality": address.locality if address else None,
                "city": address.city if address else None,
                "state": address.state if address else None,
                "postal_code": address.postal_code if address else None,
                "country": address.country if address else None,
            }

            total = thyrocare_product.selling_price * len(items)
            subtotal_amount += total

            cart_item_details.append({
                "product_type": "blood_test",
                "cart_id": item.cart_id,
                "cart_item_ids": [i.id for i in items],
                "thyrocare_product_id": thyrocare_product.id,
                "thyrocare_id": thyrocare_product.thyrocare_id,
                "product_name": thyrocare_product.name,
                "no_of_tests_included": thyrocare_product.no_of_tests_included,
                "is_fasting_required": thyrocare_product.is_fasting_required,
                "member_ids": member_ids,
                "members": members_detail,
                "members_count": len(items),
                "address": address_details,
                "price_per_member": thyrocare_product.selling_price,
                "listing_price_per_member": thyrocare_product.listing_price,
                "total_amount": total,
                "group_id": item.group_id,
                "appointment_date": str(item.appointment_date) if item.appointment_date else None,
                "appointment_start_time": item.appointment_start_time,
                "price_note": "Estimated price. Confirmed price available via /thyrocare/cart/price-breakup before checkout.",
            })

        else:
            # ---- Genetic test group (original logic — unchanged) ----
            product = item.product

            # Skip if product is deleted or missing
            if not product:
                continue

            member_ids = [i.member_id for i in items]

            # Build member-address mapping with full details for this group
            member_address_map = []
            for i in items:
                member = i.member
                address = i.address

                member_details = {
                    "member_id": member.id if member else i.member_id,
                    "name": member.name if member else "Unknown",
                    "relation": str(member.relation) if member else None,
                    "age": member.age if member else None,
                    "gender": member.gender if member else None,
                    "dob": to_ist_isoformat(member.dob) if member and member.dob else None,
                    "mobile": member.mobile if member else None
                }

                address_details = {
                    "address_id": address.id if address else i.address_id,
                    "address_label": address.address_label if address else None,
                    "street_address": address.street_address if address else None,
                    "landmark": address.landmark if address else None,
                    "locality": address.locality if address else None,
                    "city": address.city if address else None,
                    "state": address.state if address else None,
                    "postal_code": address.postal_code if address else None,
                    "country": address.country if address else None
                }

                member_address_map.append({
                    "member": member_details,
                    "address": address_details
                })

            # Get unique address IDs (may be 1 or multiple)
            address_ids = list(set(i.address_id for i in items))

            # Calculate total (quantity * price) - price is already for the plan, not per member
            total = item.quantity * product.SpecialPrice
            subtotal_amount += total

            # Calculate discount per item (difference between price and special_price)
            discount_per_item = product.Price - product.SpecialPrice

            cart_item_details.append({
                "product_type": "genetic",
                "cart_id": item.cart_id,
                "cart_item_ids": [i.id for i in items],
                "product_id": product.ProductId,
                "address_ids": address_ids,
                "member_ids": member_ids,
                "member_address_map": member_address_map,
                "product_name": product.Name,
                "product_images": product.Images,
                "plan_type": product.plan_type.value if hasattr(product.plan_type, 'value') else str(product.plan_type),
                "price": product.Price,
                "special_price": product.SpecialPrice,
                "quantity": item.quantity,
                "members_count": len(items),
                "discount_per_item": discount_per_item,
                "total_amount": total,
                "group_id": item.group_id,
            })
        # end else (genetic)

    # Get applied coupon if any
    applied_coupon = get_applied_coupon(db, current_user.id)
    coupon_amount = 0.0
    coupon_code = None
    coupon_warning = None

    # Coupon applies only to genetic products
    genetic_subtotal = sum(
        items[0].quantity * items[0].product.SpecialPrice
        for items in grouped_items.values()
        if items and str(getattr(items[0], 'product_type', '')).lower() != "blood_test" and items[0].product
    )

    if applied_coupon:
        # Re-validate and recalculate discount (cart total might have changed)
        # Pass cart_items for product type validation (e.g., FAMILYCOUPLE30 coupon)
        coupon, calculated_discount, error_message = validate_and_calculate_discount(
            db, applied_coupon.coupon_code, current_user.id, genetic_subtotal, cart_items
        )
        
        if coupon and not error_message:
            # Coupon is valid - use the recalculated discount
            coupon_amount = calculated_discount
            coupon_code = applied_coupon.coupon_code
            
            # Update the stored discount amount if it changed significantly
            if abs(applied_coupon.discount_amount - calculated_discount) > 0.01:
                applied_coupon.discount_amount = calculated_discount
                db.commit()
                logger.info(f"Updated coupon discount from {applied_coupon.discount_amount} to {calculated_discount} for user {current_user.id}")
        else:
            # Validation failed
            logger.warning(f"Coupon validation warning for '{applied_coupon.coupon_code}': {error_message}.")

            # For min order not met — keep coupon code but show 0 discount + warning
            if error_message and "minimum amount" in error_message.lower():
                coupon_amount = 0.0
                coupon_code = applied_coupon.coupon_code
                coupon_warning = error_message
            # For truly invalid cases — remove coupon entirely
            elif error_message and any(keyword in error_message.lower() for keyword in [
                "expired", "has expired", "not active", "invalid coupon code",
                "already used", "usage limit reached", "no longer available",
                "only valid for", "requires at least one"
            ]):
                logger.warning(f"Removing invalid coupon '{applied_coupon.coupon_code}'. Error: {error_message}")
                remove_coupon_from_cart(db, current_user.id)
                coupon_amount = 0.0
                coupon_code = None
                coupon_warning = error_message
            else:
                # Other temporary failures — keep stored discount
                coupon_amount = applied_coupon.discount_amount
                coupon_code = applied_coupon.coupon_code
    else:
        # No coupon applied - coupons are tracked in cart_coupons table
        # No need to check cart items for coupon codes anymore
        pass
    
    # Calculate grand total
    # subtotal_amount uses SpecialPrice, so we only subtract coupon_amount
    grand_total = subtotal_amount + delivery_charge - coupon_amount
    grand_total = max(0.0, grand_total)

    # you_save = coupon discount only
    # MRP discount is already reflected in subtotal (SpecialPrice), so we don't double-count it
    you_save = coupon_amount
    discount_amount = 0  # kept for backward compat in summary response

    # Get cart_id from cart or first item
    if not cart_id and cart_items:
        cart_id = cart_items[0].cart_id if cart_items[0].cart_id else None

    summary = {
        "cart_id": cart_id,
        "total_items": len(grouped_items),
        "total_cart_items": len(cart_items),
        "subtotal_amount": subtotal_amount,
        "discount_amount": discount_amount,
        "coupon_amount": coupon_amount,
        "coupon_code": coupon_code,
        "coupon_warning": coupon_warning,
        "you_save": you_save,
        "delivery_charge": delivery_charge,
        "grand_total": grand_total
    }

    # Audit log
    ip, user_agent = get_client_info(request)
    correlation_id = str(uuid.uuid4())
    create_audit_log(
        db=db,
        user_id=current_user.id,
        cart_id=cart_id,  # Include cart_id
        action="VIEW",
        entity_type="CART",
        details={
            "items_count": len(cart_items),
            "product_groups": len(grouped_items),
            "grand_total": grand_total,
            "cart_id": cart_id
        },
        ip_address=ip,
        user_agent=user_agent,
        username=current_user.name or current_user.mobile,
        correlation_id=correlation_id
    )

    return {
        "status": "success",
        "message": "Cart data fetched successfully.",
        "data": {
            "user_id": current_user.id,
            "username": current_user.name or current_user.mobile,
            "cart_summary": summary,
            "cart_items": cart_item_details
        }
    }


@router.post("/apply-coupon")
def apply_coupon(
    request_data: ApplyCouponRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Apply coupon code to cart (requires authentication).
    Validates coupon and calculates discount amount.
    Shows 'You Save' amount in response.
    """
    try:
        # Get user's active cart (or create if doesn't exist and has items)
        cart = db.query(Cart).filter(
            Cart.user_id == current_user.id,
            Cart.is_active == True
        ).first()
        
        # Get user's cart items to calculate subtotal (exclude deleted items)
        if cart:
            cart_items = db.query(CartItem).filter(
                CartItem.cart_id == cart.id,
                CartItem.is_deleted == False
            ).all()
        else:
            # Fallback for backward compatibility
            cart_items = db.query(CartItem).filter(
                CartItem.user_id == current_user.id,
                CartItem.is_deleted == False
            ).all()
            
            # If cart doesn't exist but user has items, create cart
            if cart_items:
                cart = get_or_create_user_cart(db, current_user.id)
                # Update items to use the new cart_id
                for item in cart_items:
                    if not item.cart_id:
                        item.cart_id = cart.id
                db.flush()  # Flush - will be committed with coupon application
        
        if not cart_items:
            raise HTTPException(
                status_code=400,
                detail="Your cart is empty. Please add items to your cart before applying a coupon."
            )
        
        cart_id = cart.id if cart else None
        
        # Calculate subtotal
        subtotal_amount = 0.0
        grouped_items = {}
        for item in cart_items:
            group_key = item.group_id or f"single_{item.id}"
            if group_key not in grouped_items:
                grouped_items[group_key] = []
            grouped_items[group_key].append(item)
        
        for group_key, items in grouped_items.items():
            if not items:
                continue
            item = items[0]
            from Cart_module.Cart_model import ProductType as PT
            if getattr(item, 'product_type', None) == PT.BLOOD_TEST:
                # Blood test items excluded from coupon subtotal
                pass
            else:
                product = item.product
                if not product:
                    continue
                subtotal_amount += item.quantity * product.SpecialPrice
        
        # Calculate product discount (informational only)
        total_product_discount = 0.0
        processed_groups = set()
        for item in cart_items:
            if not item.product:
                continue
            group_key = item.group_id or f"single_{item.id}"
            if group_key not in processed_groups:
                discount_per_item = item.product.Price - item.product.SpecialPrice
                total_product_discount += discount_per_item * item.quantity
                processed_groups.add(group_key)
        
        # Remove any previously applied coupon
        remove_coupon_from_cart(db, current_user.id)
        
        # Apply new coupon (tracked in cart_coupons table, not in cart_items)
        # Pass cart_items for product type validation (e.g., FAMILYCOUPLE30 coupon)
        success, coupon_discount_amount, message, coupon = apply_coupon_to_cart(
            db, current_user.id, request_data.coupon_code, subtotal_amount, cart_items
        )
        
        if not success:
            raise HTTPException(status_code=400, detail=message)
        
        # Coupon is now tracked in cart_coupons table, no need to update cart_items
        
        # Calculate delivery charge and grand total
        delivery_charge = 0.0
        grand_total = subtotal_amount + delivery_charge - coupon_discount_amount
        grand_total = max(0.0, grand_total)

        # you_save = coupon discount only
        # MRP discount is already reflected in subtotal (SpecialPrice), so we don't double-count it
        you_save = coupon_discount_amount
        
        # Audit log
        ip, user_agent = get_client_info(request)
        correlation_id = str(uuid.uuid4())
        create_audit_log(
            db=db,
            user_id=current_user.id,
            cart_id=cart_id,  # Include cart_id
            action="APPLY_COUPON",
            entity_type="CART",
            details={
                "coupon_code": coupon.coupon_code,
                "coupon_discount_amount": coupon_discount_amount,
                "product_discount_amount": total_product_discount,
                "subtotal": subtotal_amount,
                "grand_total": grand_total,
                "cart_id": cart_id
            },
            ip_address=ip,
            user_agent=user_agent,
            username=current_user.name or current_user.mobile,
            correlation_id=correlation_id
        )
        
        return {
            "status": "success",
            "message": message,
            "data": {
                "coupon_code": coupon.coupon_code,
                "coupon_description": coupon.description,
                "discount_type": coupon.discount_type.value,
                "discount_value": coupon.discount_value,
                "coupon_discount_amount": coupon_discount_amount,  # Discount from coupon
                "product_discount_amount": total_product_discount,  # Discount from product pricing
                "you_save": you_save,  # Total savings (product discount + coupon discount)
                "subtotal_amount": subtotal_amount,
                "delivery_charge": delivery_charge,
                "grand_total": grand_total
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Something went wrong while applying the coupon. Please try again.")


@router.delete("/remove-coupon")
def remove_coupon(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Remove applied coupon from cart (requires authentication).
    """
    try:
        # Get user's active cart for audit log (or create if doesn't exist)
        cart = db.query(Cart).filter(
            Cart.user_id == current_user.id,
            Cart.is_active == True
        ).first()
        
        # If no cart exists, create one (even if empty, for consistency)
        if not cart:
            cart = get_or_create_user_cart(db, current_user.id)
        
        cart_id = cart.id if cart else None
        
        # Remove coupon application
        removed = remove_coupon_from_cart(db, current_user.id)
        
        if not removed:
            return {
                "status": "success",
                "message": "No coupon was applied to your cart."
            }
        
        # Coupon removal is handled by remove_coupon_from_cart which removes from cart_coupons table
        # No need to update cart_items anymore
        
        # Audit log
        ip, user_agent = get_client_info(request)
        correlation_id = str(uuid.uuid4())
        create_audit_log(
            db=db,
            user_id=current_user.id,
            cart_id=cart_id,  # Include cart_id
            action="REMOVE_COUPON",
            entity_type="CART",
            details={"cart_id": cart_id},
            ip_address=ip,
            user_agent=user_agent,
            username=current_user.name or current_user.mobile,
            correlation_id=correlation_id
        )
        
        return {
            "status": "success",
            "message": "Coupon removed successfully."
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Something went wrong while removing the coupon. Please try again.")


@router.get("/list-coupons")
def list_coupons(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all available coupons that users can apply.
    Only shows coupons that:
    - Are active
    - Are within validity period
    - Have not reached their max_uses limit
    """
    try:
        coupons = db.query(Coupon).filter(
            Coupon.status == CouponStatus.ACTIVE
        ).all()
        
        coupon_list = []
        from Login_module.Utils.datetime_utils import now_ist, to_ist
        now = now_ist()
        
        for coupon in coupons:
            # Check if coupon is within validity period
            valid_from_ist = to_ist(coupon.valid_from) if coupon.valid_from else None
            valid_until_ist = to_ist(coupon.valid_until) if coupon.valid_until else None
            
            is_within_validity = (
                (not valid_from_ist or now >= valid_from_ist) and
                (not valid_until_ist or now <= valid_until_ist)
            )
            
            if not is_within_validity:
                continue

            # Check if coupon has reached its global usage limit
            if is_coupon_usage_limit_reached(db, coupon):
                continue

            # Check if this user has exhausted their per-user limit
            from Cart_module.Coupon_model import CouponUsage as _CouponUsage
            from sqlalchemy import func as _func
            max_per_user = coupon.max_uses_per_user if coupon.max_uses_per_user is not None else 1
            user_uses = db.query(_func.count(_CouponUsage.id)).filter(
                _CouponUsage.coupon_id == coupon.id,
                _CouponUsage.user_id == current_user.id
            ).scalar() or 0
            if user_uses >= max_per_user:
                continue  # This user already used up their allowance

            # Check user allowlist restriction
            if not is_user_allowed_for_coupon(db, coupon, current_user.id, current_user.mobile or ""):
                continue

            # Get current usage count for display
            current_uses = get_coupon_usage_count(db, coupon.id)
            
            coupon_list.append({
                "id": coupon.id,
                "coupon_code": coupon.coupon_code,
                "description": coupon.description,
                "status": coupon.status.value,
                "discount_type": coupon.discount_type.value,
                "discount_value": coupon.discount_value,
                "min_order_amount": coupon.min_order_amount,
                "max_discount_amount": coupon.max_discount_amount,
                "max_uses": coupon.max_uses,
                "max_uses_per_user": coupon.max_uses_per_user,
                "current_uses": current_uses,  # Show how many times it's been used
                "remaining_uses": coupon.max_uses - current_uses if coupon.max_uses else None,
                "valid_from": to_ist_isoformat(coupon.valid_from),
                "valid_until": to_ist_isoformat(coupon.valid_until),
                "created_at": to_ist_isoformat(coupon.created_at),
                "allowed_plan_types": coupon.allowed_plan_types
            })
        
        return {
            "status": "success",
            "message": f"Found {len(coupon_list)} available coupon(s)",
            "data": {
                "total_coupons": len(coupon_list),
                "coupons": coupon_list
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Something went wrong while loading available coupons. Please try again.")


@router.post("/create-coupon")
def create_coupon(
    coupon_data: CouponCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new coupon (no authentication required).
    This allows adding coupons to the database.
    """
    try:
        # Check if coupon code already exists
        existing_coupon = db.query(Coupon).filter(
            func.upper(Coupon.coupon_code) == coupon_data.coupon_code.upper()
        ).first()
        
        if existing_coupon:
            raise HTTPException(
                status_code=400,
                detail=f"Coupon code '{coupon_data.coupon_code}' already exists"
            )
        
        # Create new coupon
        new_coupon = Coupon(
            coupon_code=coupon_data.coupon_code.upper().strip(),
            description=coupon_data.description,
            discount_type=CouponType(coupon_data.discount_type),
            discount_value=coupon_data.discount_value,
            min_order_amount=coupon_data.min_order_amount,
            max_discount_amount=coupon_data.max_discount_amount,
            max_uses=coupon_data.max_uses,
            max_uses_per_user=coupon_data.max_uses_per_user,
            valid_from=coupon_data.valid_from,
            valid_until=coupon_data.valid_until,
            status=CouponStatus(coupon_data.status),
            allowed_plan_types=coupon_data.allowed_plan_types.strip().lower() if coupon_data.allowed_plan_types else None
        )
        
        db.add(new_coupon)
        db.commit()
        db.refresh(new_coupon)
        
        return {
            "status": "success",
            "message": f"Coupon '{new_coupon.coupon_code}' created successfully",
            "data": {
                "id": new_coupon.id,
                "coupon_code": new_coupon.coupon_code,
                "description": new_coupon.description,
                "discount_type": new_coupon.discount_type.value,
                "discount_value": new_coupon.discount_value,
                # user_id removed - all coupons are applicable to all users
                "min_order_amount": new_coupon.min_order_amount,
                "max_discount_amount": new_coupon.max_discount_amount,
                "max_uses": new_coupon.max_uses,
                "max_uses_per_user": new_coupon.max_uses_per_user,
                "valid_from": to_ist_isoformat(new_coupon.valid_from),
                "valid_until": to_ist_isoformat(new_coupon.valid_until),
                "status": new_coupon.status.value,
                "allowed_plan_types": new_coupon.allowed_plan_types
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Something went wrong while creating the coupon. Please try again.")



@router.post("/admin/coupons/allowlist/add")
def add_coupon_allowlist(
    request_data: AllowlistAddRequest,
    db: Session = Depends(get_db)
):
    """Add users to a coupon's allowlist (admin only)."""
    from .Coupon_model import CouponAllowedUser
    from sqlalchemy.exc import IntegrityError

    coupon = db.query(Coupon).filter(
        func.upper(Coupon.coupon_code) == request_data.coupon_code.upper().strip()
    ).first()
    if not coupon:
        raise HTTPException(status_code=404, detail=f"Coupon '{request_data.coupon_code}' not found.")

    added = 0
    added_entries = []
    for entry in request_data.entries:
        # Check for existing entry to skip duplicates
        existing = db.query(CouponAllowedUser).filter(
            CouponAllowedUser.coupon_id == coupon.id,
            (CouponAllowedUser.user_id == entry.user_id) if entry.user_id else (CouponAllowedUser.mobile == entry.mobile)
        ).first()
        if existing:
            continue
        new_entry = CouponAllowedUser(
            coupon_id=coupon.id,
            user_id=entry.user_id,
            mobile=entry.mobile
        )
        db.add(new_entry)
        try:
            db.flush()
            added += 1
            added_entries.append(new_entry)
        except IntegrityError:
            db.rollback()
    db.commit()
    for e in added_entries:
        db.refresh(e)
    return {
        "status": "success",
        "added": added,
        "entries": [{"id": e.id, "coupon_id": e.coupon_id, "user_id": e.user_id, "mobile": e.mobile} for e in added_entries]
    }


@router.delete("/admin/coupons/allowlist/remove")
def remove_coupon_allowlist(
    request_data: AllowlistRemoveRequest,
    db: Session = Depends(get_db)
):
    """Remove users from a coupon's allowlist (admin only)."""
    from .Coupon_model import CouponAllowedUser

    coupon = db.query(Coupon).filter(
        func.upper(Coupon.coupon_code) == request_data.coupon_code.upper().strip()
    ).first()
    if not coupon:
        raise HTTPException(status_code=404, detail=f"Coupon '{request_data.coupon_code}' not found.")

    removed = 0
    for entry in request_data.entries:
        if entry.user_id is not None:
            result = db.query(CouponAllowedUser).filter(
                CouponAllowedUser.coupon_id == coupon.id,
                CouponAllowedUser.user_id == entry.user_id
            ).first()
        else:
            result = db.query(CouponAllowedUser).filter(
                CouponAllowedUser.coupon_id == coupon.id,
                CouponAllowedUser.mobile == entry.mobile
            ).first()
        if result:
            db.delete(result)
            removed += 1
    db.commit()
    return {"status": "success", "removed": removed}


@router.get("/admin/coupons/allowlist")
def get_coupon_allowlist(
    coupon_code: str = Query(..., description="Coupon code to view allowlist for"),
    db: Session = Depends(get_db)
):
    """View the allowlist for a coupon (admin only)."""
    from .Coupon_model import CouponAllowedUser

    coupon = db.query(Coupon).filter(
        func.upper(Coupon.coupon_code) == coupon_code.upper().strip()
    ).first()
    if not coupon:
        raise HTTPException(status_code=404, detail=f"Coupon '{coupon_code}' not found.")

    entries = db.query(CouponAllowedUser).filter(
        CouponAllowedUser.coupon_id == coupon.id
    ).all()
    return {
        "status": "success",
        "coupon_code": coupon.coupon_code,
        "total": len(entries),
        "entries": [{"id": e.id, "coupon_id": e.coupon_id, "user_id": e.user_id, "mobile": e.mobile} for e in entries]
    }
