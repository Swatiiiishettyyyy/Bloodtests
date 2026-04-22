from .Address_model import Address
from  .Address_audit_model import AddressAudit
from sqlalchemy.orm import Session
from fastapi import HTTPException, Request
from typing import Optional, Dict, Any
import logging
import json

from .location_validator import is_serviceable_location, is_serviceable_pincode

logger = logging.getLogger(__name__)

def save_address(
    db: Session,
    user,
    req,
    request: Optional[Request] = None,
    correlation_id: Optional[str] = None,
    *,
    skip_serviceability_validation: bool = False,
    skip_cart_lock_when_editing: bool = False,
):
    """
    Save or update address (create if address_id=0, update if address_id>0).
    By default validates city/pincode against serviceable tables.
    When skip_serviceability_validation is True (e.g. Thyrocare), those checks are omitted.
    When skip_cart_lock_when_editing is True, addresses referenced by cart items may still be edited.
    """
    # Ensure country is always set (default to India)
    if not req.country or req.country.strip() == "":
        req.country = "India"

    if not skip_serviceability_validation:
        # Validate that city and state are provided (applies to both create and edit)
        if not req.city or req.city.strip() == "":
            msg = "City is required."
            logger.warning(msg)
            raise HTTPException(status_code=422, detail=msg)

        if not req.state or req.state.strip() == "":
            msg = "State is required."
            logger.warning(msg)
            raise HTTPException(status_code=422, detail=msg)

        # Validate that the city name is in serviceable locations (from serviceable_locations table)
        # This validation applies to both new addresses and address updates
        if not is_serviceable_location(req.city, None, db):
            logger.warning(
                "Address rejected for user %s: city='%s' not serviceable. Pincode: %s. "
                "City is not in serviceable locations table.",
                user.id,
                req.city,
                req.postal_code,
            )
            raise HTTPException(
                status_code=422,
                detail="Sample cannot be collected in your location. Please choose a different location."
            )

        # Validate pincode against service_locations table (if pincode provided)
        if req.postal_code and not is_serviceable_pincode(req.postal_code, db):
            logger.warning(
                "Address rejected for user %s: pincode='%s' not serviceable. City: %s.",
                user.id,
                req.postal_code,
                req.city,
            )
            raise HTTPException(
                status_code=422,
                detail="Sorry, we don't currently serve this pincode. Please enter a different one."
            )
    
    # Get IP and user agent
    ip_address = None
    user_agent = None
    if request:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
    
    old_data = None
    
    if req.address_id == 0:
        # Create new address
        address = Address(
            user_id=user.id,
            # Removed: first_name, last_name, email, mobile
            address_label=req.address_label,
            street_address=req.street_address,
            landmark=req.landmark,
            locality=req.locality,
            city=req.city,
            state=req.state,
            postal_code=req.postal_code,
            country=req.country or "India",
            save_for_future=req.save_for_future
        )
        db.add(address)
        db.commit()
        db.refresh(address)
        action = "created"
        new_data = {
            "address_id": address.id,
            "address_label": req.address_label,
            "street_address": req.street_address,
            "landmark": req.landmark,
            "locality": req.locality,
            "city": req.city,
            "state": req.state,
            "postal_code": req.postal_code,
            "country": req.country or "India",
            "save_for_future": req.save_for_future
        }
    else:
        # Edit existing address - capture old data
        address = db.query(Address).filter_by(id=req.address_id, user_id=user.id, is_deleted=False).first()
        if not address:
            return None
        
        # Check if address is associated with cart items - prevent editing if in cart
        if not skip_cart_lock_when_editing:
            from Cart_module.Cart_model import CartItem

            cart_items = db.query(CartItem).filter(
                CartItem.address_id == req.address_id,
                CartItem.user_id == user.id,
                CartItem.is_deleted == False  # Exclude deleted items
            ).all()

            if cart_items:
                product_names = set()
                for item in cart_items:
                    if item.product:
                        product_names.add(item.product.Name)

                raise HTTPException(
                    status_code=422,
                    detail="This address is currently in your cart and cannot be edited. Please remove the cart item first."
                )
        
        # Store old data before update
        old_data = {
            "address_id": address.id,
            "address_label": address.address_label,
            "street_address": address.street_address,
            "landmark": address.landmark,
            "locality": address.locality,
            "city": address.city,
            "state": address.state,
            "postal_code": address.postal_code,
            "country": address.country,
            "save_for_future": address.save_for_future
        }
        
        # Removed: first_name, last_name, email, mobile updates
        address.address_label = req.address_label
        address.street_address = req.street_address
        address.landmark = req.landmark
        address.locality = req.locality
        address.city = req.city
        address.state = req.state
        address.postal_code = req.postal_code
        address.country = req.country or "India"
        address.save_for_future = req.save_for_future
        db.commit()
        db.refresh(address)  # Refresh to get actual stored values
        
        action = "updated"  # Clear status indicating this is an edit
        
        # Use actual stored values from database for new_data to show clear changes
        new_data = {
            "address_id": address.id,
            "address_label": address.address_label,  # Use actual stored value
            "street_address": address.street_address,  # Use actual stored value
            "landmark": address.landmark,  # Use actual stored value
            "locality": address.locality,  # Use actual stored value
            "city": address.city,  # Use actual stored value
            "state": address.state,  # Use actual stored value
            "postal_code": address.postal_code,  # Use actual stored value
            "country": address.country,  # Use actual stored value
            "save_for_future": address.save_for_future  # Use actual stored value
        }

    # Audit log with IP, user_agent, and old_data/new_data
    # Determine address_label and identifier from new_data or old_data
    address_label = new_data.get("address_label") if new_data else (old_data.get("address_label") if old_data else None)
    address_city = new_data.get("city") if new_data else (old_data.get("city") if old_data else None)
    address_pincode = new_data.get("postal_code") if new_data else (old_data.get("postal_code") if old_data else None)
    address_identifier = f"{address_label} ({address_city} - {address_pincode})" if address_label and address_city and address_pincode else address_label
    
    audit = AddressAudit(
        user_id=user.id,
        username=user.name or user.mobile,
        phone_number=user.mobile,
        address_id=address.id,
        address_label=address_label,
        address_identifier=address_identifier,
        action=action,
        old_data=old_data,
        new_data=new_data,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    db.add(audit)
    db.commit()

    return address

def get_addresses_by_user(db, user):
    return db.query(Address).filter_by(user_id=user.id, is_deleted=False).all()


def delete_address(db: Session, user, address_id: int):
    """Soft delete an address"""
    from Login_module.Utils.datetime_utils import now_ist
    address = db.query(Address).filter_by(id=address_id, user_id=user.id, is_deleted=False).first()
    if not address:
        return None
    
    address.is_deleted = True
    address.deleted_at = now_ist()
    db.commit()
    return True