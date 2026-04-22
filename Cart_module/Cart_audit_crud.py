from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from .Cart_audit_model import AuditLog
from Login_module.User.user_model import User


def create_audit_log(
    db: Session,
    user_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    cart_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    username: Optional[str] = None,  # Pass username directly to avoid DB lookup
    correlation_id: Optional[str] = None  # For request tracing
) -> AuditLog:
    """
    Create an audit log entry.
    
    Args:
        db: Database session
        user_id: ID of the user performing the action
        action: Action performed (ADD, UPDATE, DELETE, VIEW, CLEAR)
        entity_type: Type of entity (CART_ITEM, CART)
        entity_id: ID of the entity (cart_item_id)
        cart_id: ID of the cart (if applicable)
        details: Additional details as dictionary
        ip_address: User's IP address
        user_agent: User's browser/device info
        username: Username (pass directly to avoid DB lookup - performance optimization)
        correlation_id: Request correlation ID for tracing related events
    """
    # Only lookup username if not provided (backward compatibility)
    if not username and user_id:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            username = user.name or user.mobile

    audit_log = AuditLog(
        user_id=user_id,
        username=username,
        cart_id=cart_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    # Add correlation_id to details if provided
    if correlation_id and details:
        details['correlation_id'] = correlation_id
    elif correlation_id:
        audit_log.details = {'correlation_id': correlation_id}
    
    db.add(audit_log)
    db.flush()

    return audit_log


def get_audit_logs_by_user(
    db: Session,
    user_id: int,
    limit: int = 100
):
    """
    Retrieve audit logs for a specific user.
    """
    return db.query(AuditLog).filter(
        AuditLog.user_id == user_id
    ).order_by(AuditLog.created_at.desc()).limit(limit).all()


def get_audit_logs_by_cart(
    db: Session,
    cart_id: int,
    limit: int = 100
):
    """
    Retrieve audit logs for a specific cart.
    """
    return db.query(AuditLog).filter(
        AuditLog.cart_id == cart_id
    ).order_by(AuditLog.created_at.desc()).limit(limit).all()