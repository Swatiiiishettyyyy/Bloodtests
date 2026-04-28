from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, status, Cookie, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import uuid
from pathlib import Path
import os
import logging

from deps import get_db

logger = logging.getLogger(__name__)
from .Member_schema import (
    MemberRequest, MemberResponse, MemberListResponse, MemberData, EditMemberRequest,
    UploadPhotoResponse, DeletePhotoResponse, MemberProfileData
)
from Audit_module.Profile_audit_crud import log_profile_update
from .Member_crud import save_member, get_members_by_user
from .Member_model import Member
from .Member_s3_service import get_member_photo_s3_service
from Login_module.Utils.auth_user import get_current_user, get_current_member
from Login_module.Utils import security
from Login_module.Utils.datetime_utils import to_ist_isoformat
from Login_module.Utils.phone_encryption import decrypt_phone
from Login_module.Device.Device_session_crud import get_device_session
from config import settings

router = APIRouter(prefix="/member", tags=["Member"])

# Standard header for passing the per-member API key from frontend to backend.
# Frontend should read `api_key` from /member/current and send it as this header
# on any request that needs to be associated with a specific member.
MEMBER_API_KEY_HEADER_NAME = "X-Member-Api-Key"
# HTTPBearer is optional; main authentication is handled by get_current_user,
# which supports both cookies (web) and Authorization header (mobile/apps).
security_scheme = HTTPBearer(auto_error=False)


def _member_order_and_flag_fields(db: Session, member_id: int) -> dict:
    """Return has_taken_genetic_test, latest_order_*, and gene_report_* for member profile. Two queries: latest order (any status) and latest Report Ready order."""
    from GeneticTest_module.GeneticTest_crud import (
        get_participant_by_member_id,
        get_latest_order_for_member,
        get_latest_report_ready_order_for_member,
    )
    participant = get_participant_by_member_id(db, member_id)
    has_taken_genetic_test = participant.has_taken_genetic_test if participant else False
    latest = get_latest_order_for_member(db, member_id)
    gene_report = get_latest_report_ready_order_for_member(db, member_id)
    return {
        "has_taken_genetic_test": has_taken_genetic_test,
        "latest_order_no": latest["order_number"] if latest else None,
        "latest_order_status": latest["order_status"] if latest else None,
        "gene_report_order_no": gene_report["order_number"] if gene_report else None,
        "gene_report_status": gene_report["order_status"] if gene_report else None,
    }


# Helper function to generate new token with selected_member_id
def generate_token_with_member(
    db: Session,
    user_id: int,
    session_id: int,
    device_platform: str,
    selected_member_id: Optional[int] = None
) -> str:
    """
    Generate a new JWT token with selected_member_id.
    Validates session before generating token.
    Raises HTTPException if session is invalid.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Validate session exists and is active
    session = get_device_session(db, session_id)
    if not session:
        logger.error(f"Session {session_id} not found when generating token for user {user_id}")
        raise HTTPException(
            status_code=401,
            detail="Session not found"
        )
    
    if not session.is_active:
        logger.error(f"Session {session_id} is inactive when generating token for user {user_id}")
        raise HTTPException(
            status_code=401,
            detail="Session is inactive"
        )
    
    # Validate member belongs to user and is not deleted if member_id is provided
    if selected_member_id:
        member = db.query(Member).filter(
            Member.id == selected_member_id,
            Member.user_id == user_id,
            Member.is_deleted == False
        ).first()
        if not member:
            logger.error(f"Member {selected_member_id} not found, doesn't belong to user {user_id}, or is deleted")
            raise HTTPException(
                status_code=404,
                detail="We couldn't find this family member profile, or it doesn't belong to your account."
            )
    
    from config import settings
    ACCESS_TOKEN_EXPIRE_SECONDS = settings.ACCESS_TOKEN_EXPIRE_SECONDS
    
    token_data = {
        "sub": str(user_id),
        "session_id": str(session_id),
        "device_platform": device_platform
    }
    if selected_member_id:
        token_data["selected_member_id"] = str(selected_member_id)
    
    try:
        return security.create_access_token(token_data, expires_delta=ACCESS_TOKEN_EXPIRE_SECONDS)
    except Exception as e:
        logger.error(f"Error creating access token for user {user_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Error generating access token"
        )

# Create new member
@router.post("/save", response_model=MemberResponse)
def save_member_api(
    req: MemberRequest,
    request: Request,
    category_id: Optional[int] = Query(None, description="Category ID (defaults to Genetic Testing)"),
    plan_type: Optional[str] = Query(None, description="Plan type: single, couple, or family"),
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    access_token_cookie: Optional[str] = Cookie(None, alias="access_token"),
    response: Response = None,
):
    """
    Create a new member (requires authentication).
    Use member_id = 0 for new members.
    Relation validation is handled by schema validator - it will reject empty or whitespace-only values.
    """
    # Set member_id to 0 for new members
    req.member_id = 0
    
    # Get IP and user agent
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())
    
    member, family_status = save_member(
        db, user, req,
        category_id=category_id,
        plan_type=plan_type,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    if not member:
        logger.error(
            f"Member creation failed - Unable to create member | "
            f"User ID: {user.id} | Category ID: {category_id} | Plan Type: {plan_type} | IP: {ip_address}"
        )
        raise HTTPException(status_code=404, detail="We couldn't create the family member profile. Please try again.")

    # Check if this is the first member for the user (check BEFORE creating to avoid race condition)
    # Count existing non-deleted members before the one we just created
    existing_member_count = db.query(Member).filter(
        Member.user_id == user.id,
        Member.id != member.id,
        Member.is_deleted == False
    ).count()
    is_first_member = existing_member_count == 0

    # Send welcome email when the first Self profile is saved
    if is_first_member and member.is_self_profile and member.email:
        try:
            import sys as _sys
            _inv_gen = str(Path(__file__).parent.parent / "invoice generation")
            if _inv_gen not in _sys.path:
                _sys.path.append(_inv_gen)
            from welcome_email import send_welcome_email
            send_welcome_email(
                to=member.email,
                name=member.name or "there",
                service_account_file=str(Path(__file__).parent.parent / settings.INVOICE_SERVICE_ACCOUNT_PATH),
                sender_email=settings.INVOICE_SENDER_EMAIL,
            )
            logger.info(f"Welcome email sent to {member.email} for new member ID {member.id} (user {user.id})")
        except Exception as e:
            logger.error(f"Welcome email failed for member {member.id} (user {user.id}): {e}", exc_info=True)

    message = "Member saved successfully."
    if family_status:
        mandatory_remaining = family_status.get("mandatory_slots_remaining", 0)
        total_members = family_status.get("total_members")
        if mandatory_remaining > 0:
            message = (
                f"Member saved. Add {mandatory_remaining} more mandatory family member(s) "
                f"to reach the required 3 (current: {total_members}/4)."
            )
        elif family_status.get("optional_slot_available", False):
            message = (
                f"Member saved. Family plan has {total_members}/4 members; "
                "you may add the optional slot."
            )
        else:
            message = "Member saved. Family plan slots are full (4/4)."

    fields = _member_order_and_flag_fields(db, member.id)
    decrypted_mobile = decrypt_phone(member.mobile) if member.mobile else None
    member_data = MemberData(
        member_id=member.id,
        name=member.name,
        relation=str(member.relation),
        age=member.age,
        gender=member.gender,
        dob=member.dob,
        mobile=decrypted_mobile,
        email=member.email,
        profile_photo_url=member.profile_photo_url,
        has_taken_genetic_test=fields["has_taken_genetic_test"],
        api_key=getattr(member, "api_key", None),
        latest_order_no=fields["latest_order_no"],
        latest_order_status=fields["latest_order_status"],
        gene_report_order_no=fields["gene_report_order_no"],
        gene_report_status=fields["gene_report_status"],
    )
    response = {
        "status": "success",
        "message": message,
        "data": member_data
    }
    # If this is the first member, auto-select it and return new token
    if is_first_member:
        try:
            # Determine token source (cookie for web, Authorization header for mobile/apps)
            token = None
            is_web_request = False
            cookie_token = access_token_cookie.strip() if access_token_cookie and isinstance(access_token_cookie, str) else None
            if cookie_token:
                # Web: token from HttpOnly cookie
                token = cookie_token
                is_web_request = True
            elif credentials and credentials.credentials:
                # Mobile/apps: token from Authorization header
                token = credentials.credentials
            else:
                token = None

            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Access token required to auto-select first member"
                )

            # Extract session information from token
            payload = security.decode_access_token(token)
            session_id = payload.get("session_id")
            device_platform = payload.get("device_platform", "unknown")
            
            if not session_id:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"No session_id in token for user {user.id} when creating first member")
            else:
                # generate_token_with_member now validates session internally
                new_token = generate_token_with_member(
                    db=db,
                    user_id=user.id,
                    session_id=int(session_id),
                    device_platform=device_platform,
                    selected_member_id=member.id
                )
                response["token"] = new_token
                response["token_type"] = "Bearer"

                # For web clients, also update the HttpOnly access_token cookie
                if is_web_request and response is not None:
                    response.set_cookie(
                        key="access_token",
                        value=new_token,
                        path="/",
                        httponly=True,
                        secure=settings.COOKIE_SECURE,
                        samesite=settings.COOKIE_SAMESITE,
                        max_age=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
                    )
        except HTTPException:
            # Re-raise HTTP exceptions (like invalid token, invalid session)
            # Don't fail member creation, but log the error
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"HTTP error generating token for first member (user {user.id}, member {member.id})", exc_info=True)
            # Continue without token - member is still created successfully
        except Exception as e:
            # Log error but don't fail the member creation
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error generating token for first member (user {user.id}, member {member.id}): {str(e)}", exc_info=True)
            # Continue without token - member is still created successfully

    return response

# Update existing member
@router.put("/edit/{member_id}", response_model=MemberResponse)
def edit_member_api(
    member_id: int,
    req: EditMemberRequest,
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Update an existing member (requires authentication).
    
    This endpoint autofills existing member details when member_id is provided.
    You can send only the fields you want to change - missing fields will be autofilled from existing member.
    
    Workflow:
    1. Send PUT request with member_id in path and only fields you want to change in body
    2. Endpoint autofills missing fields from existing member
    3. Cannot edit member if it's associated with cart items.
    4. Category and plan_type cannot be changed during edit (preserved from existing member).
    
    Example: To change only name, send: {"name": "New Name"}
    All other fields will be autofilled from existing member.
    """
    # Fetch existing member for autofill
    existing_member = db.query(Member).filter(
        Member.id == member_id,
        Member.user_id == user.id,
        Member.is_deleted == False
    ).first()
    
    if not existing_member:
        client_ip = request.client.host if request.client else None
        logger.warning(
            f"Member edit failed - Member not found or unauthorized | "
            f"Member ID: {member_id} | User ID: {user.id} | IP: {client_ip}"
        )
        raise HTTPException(status_code=404, detail="Member not found or does not belong to you")
    
    # Autofill: Merge existing member data with request data (request takes precedence)
    # Get relation value - use directly from req object (already validated and trimmed by EditMemberRequest validator)
    if req.relation is not None:
        # Relation was provided in request - use it (already validated and trimmed by validator)
        # The validator ensures it's not empty and trims it, so we can use it directly
        relation_value = req.relation
    else:
        # Relation not provided - use existing value from database
        relation_value = existing_member.relation
    
    # Ensure relation_value is not empty (safety check)
    if not relation_value or not str(relation_value).strip():
        client_ip = request.client.host if request.client else None
        logger.warning(
            f"Member edit failed - Empty relation value | "
            f"Member ID: {member_id} | User ID: {user.id} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=422,
            detail="Relation cannot be empty. Please provide a valid relation value."
        )
    
    # Convert to string and trim to ensure consistency
    relation_value = str(relation_value).strip()
    
    # Convert other fields to dict for autofill
    req_dict = req.dict(exclude_unset=True, exclude_none=True)
    
    # Decrypt existing_member.mobile if it's in the request (since it's stored encrypted)
    existing_mobile_decrypted = decrypt_phone(existing_member.mobile) if existing_member.mobile else None
    
    # Create complete MemberRequest with autofilled data
    # Fields sent in request will override existing values
    # Fields not sent will be autofilled from existing member
    complete_req = MemberRequest(
        member_id=member_id,
        name=req_dict.get('name', existing_member.name),
        relation=relation_value,  # Use the validated and trimmed relation value
        age=req_dict.get('age', existing_member.age),
        gender=req_dict.get('gender', existing_member.gender),
        dob=req_dict.get('dob', existing_member.dob),
        mobile=req_dict.get('mobile', existing_mobile_decrypted),  # Use decrypted mobile
        email=req_dict.get('email', existing_member.email)
    )
    
    # Get IP and user agent
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())
    
    # For edit, category_id and plan_type are not used (preserved from existing member)
    member, family_status = save_member(
        db, user, complete_req,
        category_id=None,  # Not used for edit
        plan_type=None,    # Not used for edit
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    if not member:
        logger.error(
            f"Member edit failed - Save operation failed | "
            f"Member ID: {member_id} | User ID: {user.id} | IP: {ip_address}"
        )
        raise HTTPException(status_code=404, detail="Member not found or does not belong to you")

    message = "Member updated successfully."
    if family_status:
        mandatory_remaining = family_status.get("mandatory_slots_remaining", 0)
        total_members = family_status.get("total_members")
        if mandatory_remaining > 0:
            message = (
                f"Member updated. Add {mandatory_remaining} more mandatory family member(s) "
                f"to reach the required 3 (current: {total_members}/4)."
            )
        elif family_status.get("optional_slot_available", False):
            message = (
                f"Member updated. Family plan has {total_members}/4 members; "
                "you may add the optional slot."
            )
        else:
            message = "Member updated. Family plan slots are full (4/4)."

    fields = _member_order_and_flag_fields(db, member.id)
    decrypted_mobile = decrypt_phone(member.mobile) if member.mobile else None
    member_data = MemberData(
        member_id=member.id,
        name=member.name,
        relation=str(member.relation),
        age=member.age,
        gender=member.gender,
        dob=member.dob,
        mobile=decrypted_mobile,
        email=member.email,
        profile_photo_url=member.profile_photo_url,
        has_taken_genetic_test=fields["has_taken_genetic_test"],
        api_key=getattr(member, "api_key", None),
        latest_order_no=fields["latest_order_no"],
        latest_order_status=fields["latest_order_status"],
        gene_report_order_no=fields["gene_report_order_no"],
        gene_report_status=fields["gene_report_status"],
    )

    return {
        "status": "success",
        "message": message,
        "data": member_data
    }


# Get list of members for user
@router.get("/list", response_model=MemberListResponse)
def get_member_list(
    category_id: Optional[int] = Query(None, description="Filter by category ID"),
    plan_type: Optional[str] = Query(None, description="Filter by plan type"),
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    members = get_members_by_user(db, user, category=category_id, plan_type=plan_type)
    data = []
    for m in members:
        relation_value = str(m.relation) if m.relation is not None else ""
        fields = _member_order_and_flag_fields(db, m.id)
        decrypted_mobile = decrypt_phone(m.mobile) if m.mobile else None
        data.append({
            "member_id": m.id,
            "name": m.name,
            "relation": relation_value,
            "age": m.age,
            "gender": m.gender,
            "dob": to_ist_isoformat(m.dob) if m.dob else None,
            "mobile": decrypted_mobile,
            "email": m.email,
            "profile_photo_url": m.profile_photo_url,
            "is_self_profile": m.is_self_profile,
            "has_taken_genetic_test": fields["has_taken_genetic_test"],
            "api_key": getattr(m, "api_key", None),
            "latest_order_no": fields["latest_order_no"],
            "latest_order_status": fields["latest_order_status"],
            "gene_report_order_no": fields["gene_report_order_no"],
            "gene_report_status": fields["gene_report_status"],
        })
    return {
        "status": "success",
        "message": "Member list fetched successfully.",
        "data": data
    }

# Delete member
@router.delete("/delete/{member_id}")
def delete_member_api(
    member_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Delete member. Prevents deletion if member is linked to cart items.
    Note: Members can be deleted even if associated with confirmed orders,
    since orders use OrderSnapshot to preserve data integrity.
    """
    from Cart_module.Cart_model import CartItem
    from Product_module.Product_model import Product
    from .Member_audit_model import MemberAuditLog
    import uuid
    
    # Verify member exists and belongs to user
    member = db.query(Member).filter(
        Member.id == member_id,
        Member.user_id == user.id,
        Member.is_deleted == False
    ).first()
    
    client_ip = request.client.host if request.client else None
    
    if not member:
        logger.warning(
            f"Member deletion failed - Member not found | "
            f"Member ID: {member_id} | User ID: {user.id} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=404,
            detail="Member not found"
        )
    
    # Prevent deletion of "Self" member (primary account holder)
    # Check using is_self_profile flag (more reliable than relation string)
    # This ensures user always has at least one member and prevents "no members" state
    if member.is_self_profile:
        logger.warning(
            f"Member deletion failed - Attempted to delete self profile | "
            f"Member ID: {member_id} | Member Name: {member.name} | User ID: {user.id} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=422,
            detail="This is your primary profile (Self). Primary profile cannot be deleted."
        )
    
    # Prevent deletion of the last remaining member (guards against mis-labeled first member)
    total_members = db.query(Member).filter(Member.user_id == user.id, Member.is_deleted == False).count()
    if total_members <= 1:
        logger.warning(
            f"Member deletion failed - Last remaining member | "
            f"Member ID: {member_id} | Member Name: {member.name} | User ID: {user.id} | Total Members: {total_members} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=422,
            detail=f"Cannot delete '{member.name}' - At least one member must remain on the account. Add another member before deleting this one."
        )
    
    # Check if member is linked to any cart items (exclude deleted items)
    cart_items = (
        db.query(CartItem, Product)
        .join(Product, CartItem.product_id == Product.ProductId)
        .filter(
            CartItem.member_id == member_id,
            CartItem.user_id == user.id,
            CartItem.is_deleted == False,  # Exclude deleted cart items
            Product.is_deleted == False
        )
        .all()
    )
    
    if cart_items:
        # Get product names and group IDs for better error message
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
        
        logger.warning(
            f"Member deletion failed - Member associated with cart items | "
            f"Member ID: {member_id} | Member Name: {member.name} | User ID: {user.id} | "
            f"Cart Items: {len(cart_items)} | Products: {conflict_details} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=422,
            detail=f"Member '{member.name}' is associated with {len(cart_items)} cart item(s) for product(s): {conflict_details}."
        )
    
    # Note: We don't check orders because confirmed orders use OrderSnapshot,
    # so deleting/editing members won't affect existing orders
    
    # Store member data before deletion for audit log
    old_data = {
        "member_id": member_id,
        "name": member.name,
        "relation": str(member.relation),  # Now a string, no need for .value check
        "age": member.age,
        "gender": member.gender,
        "dob": to_ist_isoformat(member.dob) if member.dob else None,
        "mobile": member.mobile,
        "category": member.associated_category,
        "plan_type": member.associated_plan_type
    }
    
    # Get IP and user agent for audit log
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())
    
    # Store member name before deletion
    member_name = member.name
    member_identifier = f"{member.name} ({member.mobile})"
    
    # Soft delete the member
    from Login_module.Utils.datetime_utils import now_ist
    member.is_deleted = True
    member.deleted_at = now_ist()
    
    # Create audit log for deletion BEFORE commit
    audit = MemberAuditLog(
        user_id=user.id,
        member_id=member_id,
        member_name=member_name,
        member_identifier=member_identifier,
        event_type="DELETED",
        old_data=old_data,
        new_data={"is_deleted": True, "deleted_at": to_ist_isoformat(member.deleted_at)} if member.deleted_at else {"is_deleted": True},
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    db.add(audit)
    
    # Commit both operations in a single transaction
    db.commit()
    
    # Check if deleted member was currently selected
    # Get current selection from token using credentials dependency
    selected_member_id = None
    session_id = None
    device_platform = "unknown"
    
    try:
        # Use credentials dependency for reliable token extraction
        from fastapi.security import HTTPAuthorizationCredentials
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            payload = security.decode_access_token(token)
            selected_member_id = payload.get("selected_member_id")
            session_id = payload.get("session_id")
            device_platform = payload.get("device_platform", "unknown")
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error extracting token info during member deletion: {str(e)}")
        # Continue without token update - member deletion still succeeds
    
    response = {
        "status": "success",
        "message": f"Member '{member_name}' deleted successfully."
    }
    
    # If deleted member was selected, switch to another member
    if selected_member_id and int(selected_member_id) == member_id:
        # Find another member to switch to
        remaining_member = db.query(Member).filter(
            Member.user_id == user.id,
            Member.id != member_id,
            Member.is_deleted == False
        ).order_by(Member.created_at.asc()).first()
        
        if remaining_member and session_id:
            # generate_token_with_member now validates session internally
            try:
                new_token = generate_token_with_member(
                    db=db,
                    user_id=user.id,
                    session_id=int(session_id),
                    device_platform=device_platform,
                    selected_member_id=remaining_member.id
                )
                response["token"] = new_token
                response["token_type"] = "Bearer"
                response["message"] = f"Member '{member_name}' deleted. Switched to '{remaining_member.name}' profile."
            except HTTPException:
                # Session invalid - log but don't fail deletion
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Session invalid when generating token after member deletion", exc_info=True)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error generating token after member deletion: {str(e)}", exc_info=True)
        elif not remaining_member and session_id:
            # No members left, clear selection
            # generate_token_with_member now validates session internally
            try:
                new_token = generate_token_with_member(
                    db=db,
                    user_id=user.id,
                    session_id=int(session_id),
                    device_platform=device_platform,
                    selected_member_id=None
                )
                response["token"] = new_token
                response["token_type"] = "Bearer"
            except HTTPException:
                # Session invalid - log but don't fail deletion
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Session invalid when generating token after member deletion (no members left)", exc_info=True)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error generating token after member deletion (no members left): {str(e)}", exc_info=True)
    
    return response


# Switch profile endpoint
@router.post("/select/{member_id}")
def select_member_api(
    member_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    access_token_cookie: Optional[str] = Cookie(None, alias="access_token")
):
    """
    Switch to a different member profile.
    Returns new JWT token with updated selected_member_id.
    """
    # Validate member exists and belongs to user
    member = db.query(Member).filter(
        Member.id == member_id,
        Member.user_id == user.id,
        Member.is_deleted == False
    ).first()
    
    client_ip = request.client.host if request.client else None
    
    if not member:
        logger.warning(
            f"Member selection failed - Member not found or unauthorized | "
            f"Member ID: {member_id} | User ID: {user.id} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=404,
            detail="Member not found or does not belong to you"
        )
    
    # Get current session info from token (support both cookies for web and Authorization header for mobile/apps)
    token = None
    cookie_token = access_token_cookie.strip() if access_token_cookie and isinstance(access_token_cookie, str) else None
    is_web_request = bool(cookie_token)
    if cookie_token:
        # Web: token from HttpOnly cookie
        token = cookie_token
    elif credentials and credentials.credentials and credentials.credentials.strip():
        # Mobile/apps: token from Authorization header
        token = credentials.credentials.strip()
    else:
        raise HTTPException(
            status_code=401,
            detail="Access token required"
        )

    try:
        # Decode token to extract session information
        # Note: we use decode_access_token here (no expiry tolerance) because switching profile
        # requires a valid, non-expired access token just like other authenticated actions.
        token = token.strip()
        payload = security.decode_access_token(token)
        session_id = payload.get("session_id")
        device_platform = payload.get("device_platform", "unknown")
        
        if not session_id:
            logger.warning(
                f"Member selection failed - Session ID missing in token | "
                f"Member ID: {member_id} | User ID: {user.id} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=400,
                detail="Invalid session - session_id missing in token"
            )
        
        # generate_token_with_member now validates session internally, so we don't need to validate here
        # Generate new token with selected member
        new_token = generate_token_with_member(
            db=db,
            user_id=user.id,
            session_id=int(session_id),
            device_platform=device_platform,
            selected_member_id=member_id
        )

        # For web clients, also update the HttpOnly access_token cookie so subsequent
        # requests automatically use the new selected_member_id
        if is_web_request and response is not None:
            response.set_cookie(
                key="access_token",
                value=new_token,
                path="/",
                httponly=True,
                secure=settings.COOKIE_SECURE,
                samesite=settings.COOKIE_SAMESITE,
                max_age=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
            )
        
        # Decrypt phone number for API schema
        decrypted_mobile = decrypt_phone(member.mobile) if member.mobile else None
        
        return {
            "status": "success",
            "message": f"Switched to '{member.name}' profile.",
            "data": {
                "member_id": member.id,
                "name": member.name,
                "relation": str(member.relation),
                "age": member.age,
                "gender": member.gender,
                "dob": to_ist_isoformat(member.dob) if member.dob else None,
                "mobile": decrypted_mobile,  # Decrypt before returning in schema
                "profile_photo_url": member.profile_photo_url
            },
            "token": new_token,
            "token_type": "Bearer"
        }
    except HTTPException:
        # Re-raise HTTP exceptions (invalid session, etc.)
        raise
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error switching profile for user {user.id} to member {member_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error switching profile: {str(e)}"
        )


# Get current selected member endpoint
@router.get("/current")
def get_current_member_api(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    current_member = Depends(get_current_member)
):
    """
    Get currently selected member profile.
    If no member is selected in token, returns the default member (first/self member).
    Returns member details if available, null only if user has no members.
    """
    # If no member selected in token, fall back to default member (first/self member)
    if not current_member:
        # Try to get self profile member first
        default_member = db.query(Member).filter(
            Member.user_id == current_user.id,
            Member.is_self_profile == True,
            Member.is_deleted == False
        ).first()
        
        # If no self profile, get the first member (oldest by created_at)
        if not default_member:
            default_member = db.query(Member).filter(
                Member.user_id == current_user.id,
                Member.is_deleted == False
            ).order_by(Member.created_at.asc()).first()
        
        # If still no member found, return null
        if not default_member:
            return {
                "status": "success",
                "message": "No member profile found.",
                "data": None
            }
        
        current_member = default_member
    fields = _member_order_and_flag_fields(db, current_member.id)
    decrypted_mobile = decrypt_phone(current_member.mobile) if current_member.mobile else None
    return {
        "status": "success",
        "message": "Current member profile retrieved successfully.",
        "data": {
            "user_id": current_user.id,
            "member_id": current_member.id,
            "name": current_member.name,
            "relation": str(current_member.relation),
            "age": current_member.age,
            "gender": current_member.gender,
            "dob": to_ist_isoformat(current_member.dob) if current_member.dob else None,
            "mobile": decrypted_mobile,
            "email": current_member.email,
            "profile_photo_url": current_member.profile_photo_url,
            "has_taken_genetic_test": fields["has_taken_genetic_test"],
            "api_key": getattr(current_member, "api_key", None),
            "latest_order_no": fields["latest_order_no"],
            "latest_order_status": fields["latest_order_status"],
            "gene_report_order_no": fields["gene_report_order_no"],
            "gene_report_status": fields["gene_report_status"],
        }
    }


# Allowed image file extensions
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_PHOTO_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# Content type mapping
CONTENT_TYPE_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp"
}


async def process_member_photo_upload(
    file: Optional[UploadFile],
    member: Member,
    db: Session,
    current_user,
    request: Request
) -> Optional[str]:
    """
    Helper function to process member photo upload.
    Returns the profile_photo_url if upload is successful, None otherwise.
    Raises HTTPException if validation fails.
    """
    if not file:
        return None
    
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower() if file.filename else ""
    if file_ext not in ALLOWED_PHOTO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed types: {', '.join(ALLOWED_PHOTO_EXTENSIONS)}"
        )
    
    # Read file content to check size
    file_content = await file.read()
    file_size = len(file_content)
    
    if file_size > MAX_PHOTO_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds maximum allowed size of {MAX_PHOTO_FILE_SIZE // (1024 * 1024)}MB"
        )
    
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty"
        )
    
    # Generate unique filename
    timestamp = int(uuid.uuid4().hex[:8], 16)
    filename = f"{timestamp}{file_ext}"
    
    # Determine content type
    content_type = file.content_type or CONTENT_TYPE_MAP.get(file_ext, "image/jpeg")
    
    # Delete old profile photo from S3 if exists
    if member.profile_photo_url:
        try:
            s3_service = get_member_photo_s3_service()
            s3_service.delete_member_photo(member.profile_photo_url)
        except Exception as e:
            # Log but don't fail if old photo deletion fails
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to delete old member profile photo from S3: {str(e)}")
    
    # Upload to S3
    try:
        s3_service = get_member_photo_s3_service()
        profile_photo_url = s3_service.upload_member_photo(
            member_id=member.id,
            filename=filename,
            file_content=file_content,
            content_type=content_type
        )
    except ValueError as e:
        # S3 not configured
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"S3 configuration error: {str(e)}"
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error uploading member photo to S3: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload photo to S3: {str(e)}"
        )
    
    # Store old data for audit
    old_data = {
        "profile_photo_url": member.profile_photo_url
    }
    
    # Update member profile with S3 URL
    member.profile_photo_url = profile_photo_url
    db.commit()
    db.refresh(member)
    
    # Store new data for audit
    new_data = {
        "profile_photo_url": member.profile_photo_url
    }
    
    # Audit log
    ip_address = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None
    correlation_id = str(uuid.uuid4())
    
    log_profile_update(
        db=db,
        user_id=current_user.id,
        old_data=old_data,
        new_data=new_data,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    
    return profile_photo_url


async def parse_member_request_and_file(request: Request, req_body: Optional[MemberRequest] = None) -> tuple[MemberRequest, Optional[UploadFile]]:
    """
    Parse member request from either JSON body or form data, and extract file if present.
    Returns tuple of (MemberRequest, Optional[UploadFile]).
    """
    content_type = request.headers.get("content-type", "")
    
    # If content-type is multipart, expect form data
    if "multipart/form-data" in content_type:
        form = await request.form()
        
        # Extract file if present
        file = None
        if "file" in form:
            file_obj = form["file"]
            if isinstance(file_obj, UploadFile) and file_obj.filename:
                file = file_obj
        
        name = form.get("name")
        relation = form.get("relation")
        age_str = form.get("age")
        gender = form.get("gender")
        dob_str = form.get("dob")
        mobile = form.get("mobile")
        email = form.get("email")
        
        if not name or not relation or not age_str or not gender or not dob_str or not mobile:
            client_ip = request.client.host if request and request.client else None
            logger.warning(
                f"Member request parsing failed - Missing required fields in multipart form | "
                f"IP: {client_ip}"
            )
            raise HTTPException(
                status_code=422,
                detail="When using multipart/form-data, all required fields (name, relation, age, gender, dob, mobile) must be provided as form fields."
            )
        
        try:
            age = int(age_str)
        except ValueError:
            client_ip = request.client.host if request and request.client else None
            logger.warning(
                f"Member request parsing failed - Invalid age format | "
                f"Age String: {age_str} | IP: {client_ip}"
            )
            raise HTTPException(status_code=422, detail="Invalid age format. Must be a number.")
        
        try:
            dob_date = datetime.strptime(dob_str, "%Y-%m-%d").date()
        except ValueError:
            client_ip = request.client.host if request and request.client else None
            logger.warning(
                f"Member request parsing failed - Invalid date format | "
                f"Date String: {dob_str} | IP: {client_ip}"
            )
            raise HTTPException(status_code=422, detail="Invalid date format for dob. Use YYYY-MM-DD format.")
        
        req = MemberRequest(
            member_id=0,
            name=name,
            relation=relation,
            age=age,
            gender=gender,
            dob=dob_date,
            mobile=mobile,
            email=email if email else None
        )
        return req, file
    else:
        # JSON body - use req_body if provided (parsed by FastAPI), otherwise parse manually
        if req_body is not None:
            req_body.member_id = 0
            return req_body, None
        else:
            # Try to parse from request body if not already parsed
            try:
                body = await request.json()
                req = MemberRequest(**body)
                req.member_id = 0
                return req, None
            except Exception as e:
                client_ip = request.client.host if request and request.client else None
                logger.warning(
                    f"Member request parsing failed - Invalid JSON body | "
                    f"Error: {str(e)} | IP: {client_ip}"
                )
                raise HTTPException(status_code=422, detail=f"Invalid JSON body: {str(e)}")


@router.post("/upload-photo", response_model=UploadPhotoResponse)
async def upload_member_photo(
    file: UploadFile = File(...),
    member_id: Optional[int] = Query(None, description="Optional member ID. If not provided, uses member from token or default member."),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    current_member=Depends(get_current_member)
):
    """
    Upload profile photo for a member.
    Accepts image files (jpg, jpeg, png, gif, webp) up to 5MB.
    
    If member_id is provided, uploads photo for that member (must belong to current user).
    If member_id is not provided, uses member from token or default member.
    """
    target_member = None
    
    # If member_id is provided, validate and use it
    if member_id is not None:
        target_member = db.query(Member).filter(
            Member.id == member_id,
            Member.user_id == current_user.id,
            Member.is_deleted == False
        ).first()
        
        if not target_member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Member with ID {member_id} not found or does not belong to you."
            )
    
    # If member_id not provided, use current member logic
    if target_member is None:
        if current_member:
            target_member = current_member
        else:
            # Try to get self profile member first
            default_member = db.query(Member).filter(
                Member.user_id == current_user.id,
                Member.is_self_profile == True,
                Member.is_deleted == False
            ).first()
            
            # If no self profile, get the first member (oldest by created_at)
            if not default_member:
                default_member = db.query(Member).filter(
                    Member.user_id == current_user.id,
                    Member.is_deleted == False
                ).order_by(Member.created_at.asc()).first()
            
            if not default_member:
                client_ip = request.client.host if request and request.client else None
                logger.warning(
                    f"Member photo upload failed - No member found | "
                    f"User ID: {current_user.id} | IP: {client_ip}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No member found. Please create a member profile first."
                )
            
            target_member = default_member
    
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower() if file.filename else ""
    if file_ext not in ALLOWED_PHOTO_EXTENSIONS:
        client_ip = request.client.host if request and request.client else None
        logger.warning(
            f"Member photo upload failed - Invalid file type | "
            f"Member ID: {target_member.id if target_member else None} | User ID: {current_user.id} | "
            f"File Extension: {file_ext} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed types: {', '.join(ALLOWED_PHOTO_EXTENSIONS)}"
        )
    
    # Read file content to check size
    file_content = await file.read()
    file_size = len(file_content)
    
    if file_size > MAX_PHOTO_FILE_SIZE:
        client_ip = request.client.host if request and request.client else None
        logger.warning(
            f"Member photo upload failed - File size exceeds limit | "
            f"Member ID: {target_member.id if target_member else None} | User ID: {current_user.id} | "
            f"File Size: {file_size} bytes | Max Size: {MAX_PHOTO_FILE_SIZE} bytes | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds maximum allowed size of {MAX_PHOTO_FILE_SIZE // (1024 * 1024)}MB"
        )
    
    if file_size == 0:
        client_ip = request.client.host if request and request.client else None
        logger.warning(
            f"Member photo upload failed - Empty file | "
            f"Member ID: {target_member.id if target_member else None} | User ID: {current_user.id} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty"
        )
    
    # Generate unique filename: timestamp.ext (member_id will be added by S3 service)
    timestamp = int(uuid.uuid4().hex[:8], 16)  # Use part of UUID as timestamp-like identifier
    filename = f"{timestamp}{file_ext}"
    
    # Determine content type
    content_type = file.content_type or CONTENT_TYPE_MAP.get(file_ext, "image/jpeg")
    
    # Delete old profile photo from S3 if exists
    if target_member.profile_photo_url:
        try:
            s3_service = get_member_photo_s3_service()
            s3_service.delete_member_photo(target_member.profile_photo_url)
        except Exception as e:
            # Log but don't fail if old photo deletion fails
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to delete old member profile photo from S3: {str(e)}")
    
    # Upload to S3
    try:
        s3_service = get_member_photo_s3_service()
        profile_photo_url = s3_service.upload_member_photo(
            member_id=target_member.id,
            filename=filename,
            file_content=file_content,
            content_type=content_type
        )
    except ValueError as e:
        # S3 not configured
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"S3 configuration error: {str(e)}"
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error uploading member photo to S3: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload photo to S3: {str(e)}"
        )
    
    # Store old data for audit
    old_data = {
        "profile_photo_url": target_member.profile_photo_url
    }
    
    # Update member profile with S3 URL
    target_member.profile_photo_url = profile_photo_url
    db.commit()
    db.refresh(target_member)
    # Store new data for audit
    new_data = {
        "profile_photo_url": target_member.profile_photo_url
    }
    
    # Audit log
    ip_address = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None
    correlation_id = str(uuid.uuid4())
    
    log_profile_update(
        db=db,
        user_id=current_user.id,
        old_data=old_data,
        new_data=new_data,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    
    # Decrypt phone number for API schema
    decrypted_mobile = decrypt_phone(target_member.mobile) if target_member.mobile else None
    
    fields = _member_order_and_flag_fields(db, target_member.id)
    data = MemberProfileData(
        user_id=current_user.id,
        name=target_member.name,
        email=current_user.email,
        mobile=decrypted_mobile,
        profile_photo_url=target_member.profile_photo_url,
        has_taken_genetic_test=fields["has_taken_genetic_test"],
        latest_order_no=fields["latest_order_no"],
        latest_order_status=fields["latest_order_status"],
        gene_report_order_no=fields["gene_report_order_no"],
        gene_report_status=fields["gene_report_status"],
    )
    return UploadPhotoResponse(
        status="success",
        message="Profile photo uploaded successfully.",
        data=data
    )


@router.delete("/delete-photo", response_model=DeletePhotoResponse)
async def delete_member_photo(
    member_id: Optional[int] = Query(None, description="Optional member ID. If not provided, uses member from token or default member."),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    current_member=Depends(get_current_member)
):
    """
    Delete profile photo for a member.
    
    If member_id is provided, deletes photo for that member (must belong to current user).
    If member_id is not provided, uses member from token or default member.
    """
    target_member = None
    
    # If member_id is provided, validate and use it
    if member_id is not None:
        target_member = db.query(Member).filter(
            Member.id == member_id,
            Member.user_id == current_user.id,
            Member.is_deleted == False
        ).first()
        
        if not target_member:
            client_ip = request.client.host if request and request.client else None
            logger.warning(
                f"Member photo deletion failed - Member not found or unauthorized | "
                f"Member ID: {member_id} | User ID: {current_user.id} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Member with ID {member_id} not found or does not belong to you."
            )
    
    # If member_id not provided, use current member logic
    if target_member is None:
        if current_member:
            target_member = current_member
        else:
            # Try to get self profile member first
            default_member = db.query(Member).filter(
                Member.user_id == current_user.id,
                Member.is_self_profile == True,
                Member.is_deleted == False
            ).first()
            
            # If no self profile, get the first member (oldest by created_at)
            if not default_member:
                default_member = db.query(Member).filter(
                    Member.user_id == current_user.id,
                    Member.is_deleted == False
                ).order_by(Member.created_at.asc()).first()
            
            if not default_member:
                client_ip = request.client.host if request and request.client else None
                logger.warning(
                    f"Member photo deletion failed - No member found | "
                    f"User ID: {current_user.id} | IP: {client_ip}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No member found. Please create a member profile first."
                )
            
            target_member = default_member
    
    if not target_member.profile_photo_url:
        client_ip = request.client.host if request and request.client else None
        logger.warning(
            f"Member photo deletion failed - No photo exists | "
            f"Member ID: {target_member.id} | User ID: {current_user.id} | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No photo exists. Kindly upload the profile photo."
        )
    
    # Store old data for audit
    old_data = {
        "profile_photo_url": target_member.profile_photo_url
    }
    
    # Delete file from S3
    try:
        s3_service = get_member_photo_s3_service()
        s3_service.delete_member_photo(target_member.profile_photo_url)
    except ValueError as e:
        # S3 not configured - log but don't fail deletion (photo might already be deleted)
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"S3 configuration error when deleting member profile photo: {str(e)}")
    except Exception as e:
        # Log but don't fail if S3 deletion fails (photo might already be deleted)
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to delete member profile photo from S3: {str(e)}")
    
    # Remove photo URL from database
    target_member.profile_photo_url = None
    db.commit()
    db.refresh(target_member)
    # Store new data for audit
    new_data = {
        "profile_photo_url": None
    }
    
    # Audit log
    ip_address = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None
    correlation_id = str(uuid.uuid4())
    
    log_profile_update(
        db=db,
        user_id=current_user.id,
        old_data=old_data,
        new_data=new_data,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    
    # Decrypt phone number for API schema
    decrypted_mobile = decrypt_phone(target_member.mobile) if target_member.mobile else None
    
    fields = _member_order_and_flag_fields(db, target_member.id)
    data = MemberProfileData(
        user_id=current_user.id,
        name=target_member.name,
        email=current_user.email,
        mobile=decrypted_mobile,
        profile_photo_url=None,
        has_taken_genetic_test=fields["has_taken_genetic_test"],
        latest_order_no=fields["latest_order_no"],
        latest_order_status=fields["latest_order_status"],
        gene_report_order_no=fields["gene_report_order_no"],
        gene_report_status=fields["gene_report_status"],
    )
    return DeletePhotoResponse(
        status="success",
        message="Profile photo deleted successfully.",
        data=data
    )

