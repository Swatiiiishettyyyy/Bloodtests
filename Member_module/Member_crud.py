from .Member_model import Member
from .Member_audit_model import MemberAuditLog
from sqlalchemy.orm import Session
from Login_module.Utils.datetime_utils import to_ist_isoformat
from sqlalchemy import or_, text
from fastapi import HTTPException
from typing import Any, Dict, Optional, Union
import logging

from Product_module.category_service import resolve_category

logger = logging.getLogger(__name__)

_VALID_PLAN_TYPES = {"single", "couple", "family"}


def _build_member_api_key(user_id: int, member_id: int, dob, mobile: Optional[str]) -> str:
    """Build a deterministic, human-readable member API key.

    Pattern (example): nu1532100812345
    - nu               : fixed prefix
    - 15               : day of DOB (DD)
    - 3210             : last 4 digits of mobile
    - 08               : month of DOB (MM)
    - 12               : user_id
    - 345              : member_id
    """
    # Day and month as zero-padded 2-digit strings
    day = f"{dob.day:02d}" if dob is not None else "00"
    month = f"{dob.month:02d}" if dob is not None else "00"

    # Extract digits from mobile and take last 4, padding if needed
    digits = "".join(filter(str.isdigit, str(mobile))) if mobile else ""
    if len(digits) >= 4:
        last4 = digits[-4:]
    else:
        # Pad on the left with zeros if fewer than 4 digits
        last4 = digits.zfill(4)

    return f"nu{day}{last4}{month}{user_id}{member_id}"


def _normalize_relation(relation: Optional[str]) -> str:
    """Convert incoming relation string to normalized string. Accepts any string value.
    No default value - relation must be provided by user.
    Stores exactly what user enters (trimmed), never stores empty string or whitespace.
    """
    # Ensure relation is provided
    if relation is None:
        raise ValueError("Relation is required and cannot be empty")
    
    # Convert to string (in case it's not already)
    relation_str = str(relation)
    
    # Trim the relation string
    relation_trimmed = relation_str.strip()
    
    # Ensure it's not empty after trimming (catches empty strings and whitespace-only strings)
    if not relation_trimmed:
        raise ValueError("Relation cannot be empty or whitespace only")
    
    # Return the trimmed value - store exactly what user enters
    # No enum restriction - user can enter any relation string
    return relation_trimmed


def _normalize_plan_type(plan_type: Optional[str]) -> Optional[str]:
    if not plan_type:
        return None
    plan = plan_type.strip().lower()
    if plan not in _VALID_PLAN_TYPES:
        logger.warning(
            f"Plan type validation failed - Unsupported plan type | "
            f"Plan Type: {plan_type} | Allowed: {_VALID_PLAN_TYPES}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported plan_type '{plan_type}'. Allowed values: single, couple, family.",
        )
    return plan


def _family_member_count(db: Session, user_id: int, category_filter, exclude_member_id: Optional[int] = None) -> int:
    """Count current family-plan members for a user/category."""
    query = db.query(Member).filter(
        Member.user_id == user_id,
        Member.associated_plan_type == "family",
        category_filter,
        Member.is_deleted == False
    )
    if exclude_member_id:
        query = query.filter(Member.id != exclude_member_id)
    return query.count()


def _build_family_plan_status(db: Session, user_id: int, member: Member) -> Optional[Dict[str, Any]]:
    """Return family-plan progress details for the member's category."""
    if member.associated_plan_type != "family":
        return None

    category_filter = or_(
        Member.associated_category_id == member.associated_category_id,
        Member.associated_category == member.associated_category,
    )
    total_members = _family_member_count(db, user_id, category_filter)
    mandatory_slots_remaining = max(0, 3 - total_members)
    optional_slot_available = total_members < 4

    if mandatory_slots_remaining > 0:
        status = "incomplete"
    elif optional_slot_available:
        status = "ready"
    else:
        status = "full"

    return {
        "total_members": total_members,
        "mandatory_slots_remaining": mandatory_slots_remaining,
        "optional_slot_available": optional_slot_available,
        "status": status,
    }

def save_member(
    db: Session,
    user,
    req,
    category_id: Optional[int] = None,
    plan_type: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Save or update member (create if member_id=0, update if member_id>0).
    For new members: category_id and plan_type are required.
    For existing members: category_id and plan_type are ignored (preserved from existing member).
    Validates that member is not already in a conflicting plan type.
    Prevents editing if member is associated with cart items.
    """
    plan_type_normalized = _normalize_plan_type(plan_type)
    family_status: Optional[Dict[str, Any]] = None

    category_obj = resolve_category(db, category_id)
    category_name = category_obj.name
    category_filter = or_(
        Member.associated_category_id == category_obj.id,
        Member.associated_category == category_name
    )

    # Get relation value from request - it's already validated and trimmed by schema validator
    # The validator ensures it's not empty and trims it, so we can use it directly
    # Store exactly what user enters (already trimmed by validator)
    
    # Get relation value directly from request object
    # The Pydantic validator has already validated and trimmed it
    if not hasattr(req, 'relation'):
        raise ValueError("Relation field is missing from request object")
    
    # Get the relation value - validator should have already validated and trimmed it
    relation_value = req.relation
    
    if relation_value is None:
        raise ValueError("Relation is required and cannot be None")
    
    # Convert to string and ensure it's trimmed (validator should have done this)
    relation_to_store = str(relation_value).strip()
    
    # CRITICAL: Final safety check - ensure relation is not empty
    # This should never happen if validator is working, but we check anyway
    if not relation_to_store:
        # Get additional debug info
        debug_info = f"req.relation={repr(req.relation)}, type={type(req.relation)}"
        try:
            if hasattr(req, 'dict'):
                debug_info += f", dict={req.dict()}"
        except:
            pass
        
        raise HTTPException(
            status_code=422,
            detail=(
                f"Relation cannot be empty. The validator should have prevented this. "
                f"Debug info: {debug_info}. "
                f"Please ensure you are sending a non-empty relation value in your request."
            )
        )
    
    # Store the relation exactly as entered (trimmed) - no enum restrictions, accepts any string value

    # Check for duplicate member in same category
    if req.member_id == 0:  # New member
        # First check if active member with same name AND relation already exists in same category
        # This allows same name with different relations (e.g., "John" as self and "John" as child)
        existing = db.query(Member).filter(
            Member.user_id == user.id,
            Member.name == req.name,
            Member.relation == relation_to_store,
            category_filter,
            Member.is_deleted == False
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=422,
                detail="This member already exists."
            )
        
        # Check if deleted member with same name AND relation exists in same category
        # If found, restore it instead of creating new member
        deleted_member = db.query(Member).filter(
            Member.user_id == user.id,
            Member.name == req.name,
            Member.relation == relation_to_store,
            category_filter,
            Member.is_deleted == True
        ).first()
        
        if deleted_member:
            # Store old deleted_at value for audit log before clearing it
            old_deleted_at = to_ist_isoformat(deleted_member.deleted_at) if deleted_member.deleted_at else None
            
            # Preserve original data that should not be overwritten
            preserved_profile_photo_url = deleted_member.profile_photo_url
            preserved_is_self_profile = deleted_member.is_self_profile
            preserved_created_at = deleted_member.created_at
            
            # Restore the deleted member
            deleted_member.is_deleted = False
            deleted_member.deleted_at = None
            
            # Update fields from request (user may want to update some info)
            deleted_member.age = req.age
            deleted_member.gender = req.gender
            deleted_member.dob = req.dob
            deleted_member.mobile = req.mobile  # Store as plain text
            if hasattr(req, 'email'):
                deleted_member.email = getattr(req, 'email', None)

            # Ensure restored member has an API key; build from current fields if missing
            if not getattr(deleted_member, "api_key", None):
                deleted_member.api_key = _build_member_api_key(
                    user_id=deleted_member.user_id,
                    member_id=deleted_member.id,
                    dob=deleted_member.dob,
                    mobile=deleted_member.mobile,
                )
            
            # Preserve profile photo URL (restore original photo if it existed)
            deleted_member.profile_photo_url = preserved_profile_photo_url
            
            # Preserve is_self_profile flag (don't change primary profile status)
            deleted_member.is_self_profile = preserved_is_self_profile
            
            # Update category and plan type if provided (allows moving to different plan)
            if category_obj:
                deleted_member.associated_category = category_name
                deleted_member.associated_category_id = category_obj.id
            if plan_type_normalized:
                deleted_member.associated_plan_type = plan_type_normalized
            
            # Update updated_at timestamp
            from Login_module.Utils.datetime_utils import now_ist
            deleted_member.updated_at = now_ist()
            
            db.commit()
            db.refresh(deleted_member)
            
            # Build family status if applicable
            if deleted_member.associated_plan_type == "family":
                family_status = _build_family_plan_status(db, user.id, deleted_member)
            else:
                family_status = None
            
            # Create audit log for restoration
            # Note: MemberAuditLog is already imported at the top of the file
            audit = MemberAuditLog(
                user_id=user.id,
                member_id=deleted_member.id,
                member_name=deleted_member.name,
                member_identifier=f"{deleted_member.name} ({deleted_member.mobile})",
                event_type="RESTORED",
                old_data={"is_deleted": True, "deleted_at": old_deleted_at},
                new_data={"is_deleted": False, "deleted_at": None},
                ip_address=ip_address,
                user_agent=user_agent,
                correlation_id=correlation_id
            )
            db.add(audit)
            db.commit()
            
            
            # Return restored member (skip the rest of the creation logic)
            return deleted_member, family_status
        
        # If adding to family plan, check if member is already in personal/couple plan
        if plan_type_normalized == "family":
            conflicting_member = db.query(Member).filter(
                Member.user_id == user.id,
                Member.name == req.name,
                category_filter,
                Member.associated_plan_type.in_(["single", "couple"]),
                Member.is_deleted == False
            ).first()
            
            if conflicting_member:
                plan = conflicting_member.associated_plan_type or "another plan"
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{req.name} is already in your {plan} plan for {category_name}. "
                        f"Please remove them there before adding again."
                    )
                )

            current_family_members = _family_member_count(db, user.id, category_filter)
            if current_family_members >= 4:
                raise HTTPException(
                    status_code=400,
                    detail="Family plan allows up to 4 members (3 mandatory + 1 optional). Remove an existing member before adding a new one."
                )
        else:
            # If adding to personal/couple, check if member is in family plan
            conflicting_member = db.query(Member).filter(
                Member.user_id == user.id,
                Member.name == req.name,
                category_filter,
                Member.associated_plan_type == "family",
                Member.is_deleted == False
            ).first()
            
            if conflicting_member:
                plan = conflicting_member.associated_plan_type or "family"
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{req.name} is already in your {plan} plan for {category_name}. "
                        f"Please remove them there before adding again."
                    )
                )
    
    old_data = None
    
    # If editing existing member, check if member is in cart
    if req.member_id != 0:
        from Cart_module.Cart_model import CartItem
        from Product_module.Product_model import Product
        
        # Check if member is linked to any cart items (exclude deleted items)
        cart_items = (
            db.query(CartItem, Product)
            .join(Product, CartItem.product_id == Product.ProductId)
            .filter(
                CartItem.member_id == req.member_id,
                CartItem.user_id == user.id,
                CartItem.is_deleted == False,  # Exclude deleted cart items
                Product.is_deleted == False
            )
            .all()
        )
        
        if cart_items:
            # Get product names and plan types for better error message
            conflicts = []
            for cart_item, product in cart_items:
                conflicts.append({
                    "product_id": product.ProductId,
                    "product_name": product.Name,
                    "plan_type": product.plan_type.value if hasattr(product.plan_type, 'value') else str(product.plan_type)
                })
            
            # Group by product
            from collections import defaultdict
            product_conflicts = defaultdict(list)
            for conflict in conflicts:
                product_conflicts[conflict["product_name"]].append(conflict["plan_type"])
            
            conflict_details = ", ".join([
                f"{name} ({'/'.join(set(types))})" 
                for name, types in product_conflicts.items()
            ])
            
            raise HTTPException(
                status_code=422,
                detail=f"'{req.name}' is currently in your cart and cannot be edited. Please remove them from your cart first."
            )
    
    if req.member_id == 0:
        # Create new member
        # CRITICAL: Ensure relation_to_store is not empty before creating member
        if not relation_to_store or len(relation_to_store.strip()) == 0:
            raise ValueError(
                f"Cannot create member with empty relation. "
                f"relation_to_store: '{relation_to_store}', "
                f"req.relation: '{req.relation}'"
            )
        
        # Check if this is the first member for the user
        existing_member_count = db.query(Member).filter(
            Member.user_id == user.id,
            Member.is_deleted == False
        ).count()
        is_first_member = existing_member_count == 0
        
        # Determine if this should be marked as self profile
        # Set is_self_profile = True if:
        # 1. This is the first member for the user, AND
        # 2. Relation is "Self" (case-insensitive check)
        relation_lower = relation_to_store.strip().lower()
        should_mark_as_self = is_first_member and relation_lower in ["self", "user", "account holder", "account_holder"]
        
        # For first member with "Self" relation, auto-populate mobile from user's login mobile
        # Extract last 10 digits from user.mobile to match member schema validation (exactly 10 digits)
        member_mobile = req.mobile
        if should_mark_as_self and user.mobile:
            # Extract digits only from user.mobile
            user_mobile_digits = ''.join(filter(str.isdigit, str(user.mobile)))
            # Take last 10 digits (handles cases where user.mobile might have country code)
            if len(user_mobile_digits) >= 10:
                member_mobile = user_mobile_digits[-10:]
            elif len(user_mobile_digits) > 0:
                # If less than 10 digits, use as-is (fallback - should not happen in normal flow)
                member_mobile = user_mobile_digits
        
        # Create member with the relation value
        # IMPORTANT: relation_to_store has been validated and is guaranteed to be non-empty
        # API key is built after we have a member ID (post-flush) using the agreed deterministic pattern.
        member = Member(
            user_id=user.id,
            name=req.name,
            relation=relation_to_store,  # Store the validated and trimmed relation value
            age=req.age,
            gender=req.gender,
            dob=req.dob,
            mobile=member_mobile,  # Store as plain text
            email=getattr(req, 'email', None),  # Optional email field
            associated_category=category_name,
            associated_category_id=category_obj.id,
            associated_plan_type=plan_type_normalized,
            is_self_profile=should_mark_as_self,  # Mark as self profile if first member with "Self" relation
        )
        db.add(member)
        db.flush()  # Flush to get the ID without committing

        # Now that member.id is available, build the deterministic API key
        member.api_key = _build_member_api_key(
            user_id=user.id,
            member_id=member.id,
            dob=member.dob,
            mobile=member.mobile,
        )
        
        # CRITICAL: Verify the relation was set correctly BEFORE commit
        if not member.relation or str(member.relation).strip() == "":
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"CRITICAL ERROR: Relation was set to empty value in member object! "
                    f"Expected: '{relation_to_store}', Got: '{member.relation}'. "
                    f"This should never happen. Please check the database schema and constraints."
                )
            )
        
        db.commit()
        db.refresh(member)
        
        # WORKAROUND: If relation is empty after commit, explicitly update it
        # This handles cases where database triggers or defaults might be interfering
        if not member.relation or str(member.relation).strip() == "":
            # Check the database column type to understand the issue
            column_info = None
            is_enum = False
            try:
                # Try to get column information (MySQL/PostgreSQL)
                if hasattr(db.bind, 'url') and 'mysql' in str(db.bind.url):
                    column_info = db.execute(
                        text("""
                            SELECT COLUMN_TYPE, COLUMN_DEFAULT, IS_NULLABLE 
                            FROM INFORMATION_SCHEMA.COLUMNS 
                            WHERE TABLE_SCHEMA = DATABASE() 
                            AND TABLE_NAME = 'members' 
                            AND COLUMN_NAME = 'relation'
                        """)
                    ).fetchone()
                    if column_info and 'enum' in str(column_info[0]).lower():
                        is_enum = True
                        # Try to fix it by altering the column to VARCHAR
                        try:
                            db.execute(text("ALTER TABLE members MODIFY COLUMN relation VARCHAR(50) NOT NULL"))
                            db.commit()
                        except Exception as alter_error:
                            pass  # If alter fails, continue with update attempt
                elif hasattr(db.bind, 'url') and 'postgresql' in str(db.bind.url):
                    column_info = db.execute(
                        text("""
                            SELECT data_type, column_default, is_nullable 
                            FROM information_schema.columns 
                            WHERE table_name = 'members' 
                            AND column_name = 'relation'
                        """)
                    ).fetchone()
                    if column_info and 'enum' in str(column_info[0]).lower():
                        is_enum = True
                        # Try to fix it by altering the column to VARCHAR
                        try:
                            db.execute(text("ALTER TABLE members ALTER COLUMN relation TYPE VARCHAR(50)"))
                            db.execute(text("ALTER TABLE members ALTER COLUMN relation SET NOT NULL"))
                            db.commit()
                        except Exception as alter_error:
                            pass  # If alter fails, continue with update attempt
            except Exception as e:
                pass  # Ignore errors in diagnostic query
            
            # Try direct UPDATE with the value
            db.execute(
                text("UPDATE members SET relation = :relation WHERE id = :id"),
                {"relation": relation_to_store, "id": member.id}
            )
            db.commit()
            
            # Verify by querying directly from database
            db_result = db.execute(
                text("SELECT relation FROM members WHERE id = :id"),
                {"id": member.id}
            ).fetchone()
            
            # Refresh the member object
            db.refresh(member)
            
            # Check both the direct query and the refreshed object
            db_value = db_result[0] if db_result and db_result[0] else None
            obj_value = member.relation if member.relation else None
            
            if (not db_value or str(db_value).strip() == "") and (not obj_value or str(obj_value).strip() == ""):
                # Get more diagnostic info
                db_type = "Unknown"
                if hasattr(db.bind, 'url'):
                    db_type = str(db.bind.url).split('://')[0] if '://' in str(db.bind.url) else str(db.bind.url)
                
                error_msg = (
                    f"CRITICAL ERROR: Unable to store relation value in database!\n"
                    f"Expected: '{relation_to_store}'\n"
                    f"Database value: '{db_value}'\n"
                    f"Object value: '{obj_value}'\n"
                    f"Database type: {db_type}\n"
                )
                
                if column_info:
                    error_msg += f"Column definition: {column_info}\n"
                    if is_enum:
                        error_msg += f"WARNING: Column is defined as ENUM type!\n"
                
                error_msg += (
                    f"\nThis indicates a database-level issue. The 'relation' column may be defined as ENUM.\n\n"
                    f"SOLUTION: Run this SQL to fix the column:\n"
                )
                
                if 'mysql' in db_type.lower():
                    error_msg += f"ALTER TABLE members MODIFY COLUMN relation VARCHAR(50) NOT NULL;\n"
                elif 'postgresql' in db_type.lower():
                    error_msg += (
                        f"ALTER TABLE members ALTER COLUMN relation TYPE VARCHAR(50);\n"
                        f"ALTER TABLE members ALTER COLUMN relation SET NOT NULL;\n"
                    )
                else:
                    error_msg += f"ALTER TABLE members ALTER COLUMN relation VARCHAR(50) NOT NULL;\n"
                
                raise HTTPException(status_code=500, detail=error_msg)
        
        # If this is the first member with "Self" relation (is_self_profile=True),
        # update user table fields from member details
        if should_mark_as_self:
            user.name = req.name
            if getattr(req, 'email', None):
                new_email = req.email.strip() if req.email else None
                if new_email:
                    # Check if email already exists for a different user
                    from Login_module.User.user_model import User
                    existing_user = db.query(User).filter(
                        User.email == new_email,
                        User.id != user.id  # Exclude current user
                    ).first()
                    
                    if existing_user:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Given email already exists for different user"
                        )
                    
                    # Email is unique, safe to update
                    user.email = new_email
            # Update profile_photo_url if provided in member (though it's not in MemberRequest schema currently)
            # This allows future extensibility if profile_photo_url is added to the request
            if hasattr(member, 'profile_photo_url') and member.profile_photo_url:
                user.profile_photo_url = member.profile_photo_url
            
            # Try to flush user updates, catch any IntegrityError
            try:
                db.flush()  # Flush user updates before committing audit log
            except Exception as e:
                # Check if it's a duplicate email error
                error_str = str(e).lower()
                if 'duplicate entry' in error_str and 'email' in error_str:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Given email already exists for different user"
                    )
                # Re-raise other exceptions
                raise
        
        # Audit log for creation
        new_data = {
            "member_id": member.id,
            "name": req.name,
            "relation": req.relation,
            "age": req.age,
            "gender": req.gender,
            "dob": to_ist_isoformat(req.dob) if req.dob else None,
            "mobile": req.mobile,
            "email": getattr(req, 'email', None),
            "category": category_name,
            "plan_type": plan_type_normalized
        }
        # Note: new_data already has req.mobile (plaintext) which is fine for audit logs
        audit = MemberAuditLog(
            user_id=user.id,
            member_id=member.id,
            member_name=req.name,
            member_identifier=f"{req.name} ({req.mobile})",
            event_type="CREATED",
            new_data=new_data,
            ip_address=ip_address,
            user_agent=user_agent,
            correlation_id=correlation_id
        )
        db.add(audit)
        db.commit()
    else:
        # Update existing member
        member = db.query(Member).filter_by(id=req.member_id, user_id=user.id).first()
        if not member:
            return None, None
        
        # Store old data before update
        old_data = {
            "member_id": member.id,
            "name": member.name,
            "relation": str(member.relation),  # Now a string, no need for .value check
            "age": member.age,
            "gender": member.gender,
            "dob": to_ist_isoformat(member.dob) if member.dob else None,
            "mobile": member.mobile,
            "email": member.email,
            "category": member.associated_category,
            "plan_type": member.associated_plan_type
        }
        
        # Update member fields (category and plan_type are preserved from existing member)
        # Ensure relation_to_store is not empty before updating
        if not relation_to_store or len(relation_to_store.strip()) == 0:
            raise ValueError(f"Cannot update member with empty relation. Received: '{req.relation}'")
        
        # Prevent changing is_self_profile from True to False (primary profile protection)
        # Once a member is marked as self profile, it should remain so
        # is_self_profile is not updated during edit - it remains as set during creation
        
        member.name = req.name
        member.relation = relation_to_store  # Store the validated and trimmed relation value
        member.age = req.age
        member.gender = req.gender
        member.dob = req.dob
        member.mobile = req.mobile  # Store as plain text
        member.email = getattr(req, 'email', None)  # Optional email field
        # Note: category_id and plan_type are not updated on edit to maintain data integrity
        # Note: is_self_profile is not updated on edit to protect primary profile
        
        # If this is a self-profile member, also update user table fields
        if member.is_self_profile:
            user.name = req.name
            if getattr(req, 'email', None):
                new_email = req.email.strip() if req.email else None
                if new_email:
                    # Check if email already exists for a different user
                    from Login_module.User.user_model import User
                    existing_user = db.query(User).filter(
                        User.email == new_email,
                        User.id != user.id  # Exclude current user
                    ).first()
                    
                    if existing_user:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Given email already exists for different user"
                        )
                    
                    # Email is unique, safe to update
                    user.email = new_email
        
        # Flush changes to database before commit to ensure they're tracked
        # Catch any IntegrityError for duplicate email
        try:
            db.flush()
            db.commit()
        except Exception as e:
            # Check if it's a duplicate email error
            error_str = str(e).lower()
            if 'duplicate entry' in error_str and 'email' in error_str:
                db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Given email already exists for different user"
                )
            # Re-raise other exceptions
            raise
        db.refresh(member)  # Refresh to get latest values from database
        
        # WORKAROUND: If relation is empty after commit, explicitly update it
        # This handles cases where database triggers or defaults might be interfering
        if not member.relation or str(member.relation).strip() == "":
            # Check the database column type to understand the issue
            column_info = None
            is_enum = False
            try:
                # Try to get column information (MySQL/PostgreSQL)
                if hasattr(db.bind, 'url') and 'mysql' in str(db.bind.url):
                    column_info = db.execute(
                        text("""
                            SELECT COLUMN_TYPE, COLUMN_DEFAULT, IS_NULLABLE 
                            FROM INFORMATION_SCHEMA.COLUMNS 
                            WHERE TABLE_SCHEMA = DATABASE() 
                            AND TABLE_NAME = 'members' 
                            AND COLUMN_NAME = 'relation'
                        """)
                    ).fetchone()
                    if column_info and 'enum' in str(column_info[0]).lower():
                        is_enum = True
                        # Try to fix it by altering the column to VARCHAR
                        try:
                            db.execute(text("ALTER TABLE members MODIFY COLUMN relation VARCHAR(50) NOT NULL"))
                            db.commit()
                        except Exception as alter_error:
                            pass  # If alter fails, continue with update attempt
                elif hasattr(db.bind, 'url') and 'postgresql' in str(db.bind.url):
                    column_info = db.execute(
                        text("""
                            SELECT data_type, column_default, is_nullable 
                            FROM information_schema.columns 
                            WHERE table_name = 'members' 
                            AND column_name = 'relation'
                        """)
                    ).fetchone()
                    if column_info and 'enum' in str(column_info[0]).lower():
                        is_enum = True
                        # Try to fix it by altering the column to VARCHAR
                        try:
                            db.execute(text("ALTER TABLE members ALTER COLUMN relation TYPE VARCHAR(50)"))
                            db.execute(text("ALTER TABLE members ALTER COLUMN relation SET NOT NULL"))
                            db.commit()
                        except Exception as alter_error:
                            pass  # If alter fails, continue with update attempt
            except Exception as e:
                pass  # Ignore errors in diagnostic query
            
            # Try direct UPDATE with the value
            db.execute(
                text("UPDATE members SET relation = :relation WHERE id = :id"),
                {"relation": relation_to_store, "id": member.id}
            )
            db.commit()
            
            # Verify by querying directly from database
            db_result = db.execute(
                text("SELECT relation FROM members WHERE id = :id"),
                {"id": member.id}
            ).fetchone()
            
            # Refresh the member object
            db.refresh(member)
            
            # Check both the direct query and the refreshed object
            db_value = db_result[0] if db_result and db_result[0] else None
            obj_value = member.relation if member.relation else None
            
            if (not db_value or str(db_value).strip() == "") and (not obj_value or str(obj_value).strip() == ""):
                # Get more diagnostic info
                db_type = "Unknown"
                if hasattr(db.bind, 'url'):
                    db_type = str(db.bind.url).split('://')[0] if '://' in str(db.bind.url) else str(db.bind.url)
                
                error_msg = (
                    f"CRITICAL ERROR: Unable to update relation value in database!\n"
                    f"Expected: '{relation_to_store}'\n"
                    f"Database value: '{db_value}'\n"
                    f"Object value: '{obj_value}'\n"
                    f"Database type: {db_type}\n"
                )
                
                if column_info:
                    error_msg += f"Column definition: {column_info}\n"
                    if is_enum:
                        error_msg += f"WARNING: Column is defined as ENUM type!\n"
                
                error_msg += (
                    f"\nThis indicates a database-level issue. The 'relation' column may be defined as ENUM.\n\n"
                    f"SOLUTION: Run this SQL to fix the column:\n"
                )
                
                if 'mysql' in db_type.lower():
                    error_msg += f"ALTER TABLE members MODIFY COLUMN relation VARCHAR(50) NOT NULL;\n"
                elif 'postgresql' in db_type.lower():
                    error_msg += (
                        f"ALTER TABLE members ALTER COLUMN relation TYPE VARCHAR(50);\n"
                        f"ALTER TABLE members ALTER COLUMN relation SET NOT NULL;\n"
                    )
                else:
                    error_msg += f"ALTER TABLE members ALTER COLUMN relation VARCHAR(50) NOT NULL;\n"
                
                raise HTTPException(status_code=500, detail=error_msg)
        
        # Audit log for update - use actual stored values from database
        # Refresh member to ensure we have the latest values after any workarounds
        db.refresh(member)
        
        # Prepare new data for audit log
        new_data = {
            "member_id": member.id,
            "name": member.name,  # Use actual stored value
            "relation": str(member.relation),  # Use actual stored value (after any workarounds)
            "age": member.age,  # Use actual stored value
            "gender": member.gender,  # Use actual stored value
            "dob": to_ist_isoformat(member.dob) if member.dob else None,  # Use actual stored value
            "mobile": member.mobile,  # Use plain text value for audit log
            "category": member.associated_category,
            "plan_type": member.associated_plan_type
        }
        
        audit = MemberAuditLog(
            user_id=user.id,
            member_id=member.id,
            member_name=member.name,  # Use actual stored value
                member_identifier=f"{member.name} ({member.mobile})",  # Use plain text value
            event_type="UPDATED",  # Clear status indicating this is an edit
            old_data=old_data,  # Previous values before update
            new_data=new_data,  # New values after update - shows clear changes
            ip_address=ip_address,
            user_agent=user_agent,
            correlation_id=correlation_id
        )
        db.add(audit)
        db.commit()

    family_status = _build_family_plan_status(db, user.id, member)
    
    
    return member, family_status

def get_members_by_user(db: Session, user, category: Optional[Union[int, str]] = None, plan_type: Optional[str] = None):
    """Get members for user, optionally filtered by category and plan_type"""
    import logging
    logger = logging.getLogger(__name__)
    
    # Ensure user has an id attribute
    user_id = user.id if hasattr(user, 'id') else user
    if not user_id:
        logger.error(f"get_members_by_user: Invalid user object - no id attribute. User: {user}")
        return []
    
    # Debug: Log query parameters
    logger.info(f"get_members_by_user called: user_id={user_id}, category={category}, plan_type={plan_type}")
    
    # Build base query - check both user_id types (int vs object)
    query = db.query(Member).filter(
        Member.user_id == user_id,
        Member.is_deleted == False
    )
    
    if category:
        if isinstance(category, int):
            query = query.filter(Member.associated_category_id == category)
        else:
            query = query.filter(Member.associated_category == category)
    if plan_type:
        query = query.filter(Member.associated_plan_type == plan_type)
    
    members = query.all()
    
    # Debug: Log raw query results with detailed info
    logger.info(f"Query returned {len(members)} members for user_id={user_id}")
    
    if len(members) == 0:
        # Check if members exist but are deleted or belong to different user
        all_members_count = db.query(Member).filter(Member.user_id == user_id).count()
        deleted_members_count = db.query(Member).filter(
            Member.user_id == user_id,
            Member.is_deleted == True
        ).count()
        active_members_count = db.query(Member).filter(
            Member.user_id == user_id,
            Member.is_deleted == False
        ).count()
        
        # Check if category/plan_type filter is too restrictive
        if category or plan_type:
            unfiltered_count = db.query(Member).filter(
                Member.user_id == user_id,
                Member.is_deleted == False
            ).count()
            logger.warning(
                f"No members found with filters | "
                f"User ID: {user_id} | Category: {category} | Plan Type: {plan_type} | "
                f"Unfiltered active members: {unfiltered_count} | "
                f"Total members: {all_members_count} | Deleted: {deleted_members_count} | Active: {active_members_count}"
            )
        else:
            logger.warning(
                f"No active members found for user_id={user_id}. "
                f"Total members: {all_members_count}, Deleted: {deleted_members_count}, Active: {active_members_count}"
            )
    
    
    return members