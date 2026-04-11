"""
Genetic Test Participant CRUD operations.
Handles tracking of genetic test participants.
"""
from sqlalchemy.orm import Session
from typing import Optional, List
import logging

from .GeneticTest_model import GeneticTestParticipant

logger = logging.getLogger(__name__)


def get_latest_order_for_member(
    db: Session,
    member_id: int
) -> Optional[dict]:
    """
    Most recent genetic test order for this member (any status), by order date then id.
    Returns dict with order_id, order_number, order_status or None.
    """
    from Orders_module.Order_model import Order, OrderItem

    order = (
        db.query(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .filter(
            OrderItem.member_id == member_id,
            OrderItem.product_id != None,  # genetic test items only
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
        .first()
    )
    if not order:
        return None
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "order_status": order.order_status.value if hasattr(order.order_status, "value") else str(order.order_status),
    }


def get_latest_report_ready_order_for_member(
    db: Session,
    member_id: int
) -> Optional[dict]:
    """
    Most recent order where this member has at least one item with REPORT_READY (or COMPLETED).
    Uses OrderItem.order_status, not Order.order_status, so wife can get gene report
    even when husband (same order, different address) is still in SCHEDULED/SAMPLE_COLLECTED.
    Returns dict with order_id, order_number, order_status or None.
    """
    from Orders_module.Order_model import Order, OrderItem, OrderStatus

    order = (
        db.query(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .filter(
            OrderItem.member_id == member_id,
            OrderItem.product_id != None,  # genetic test items only
            OrderItem.order_status.in_([OrderStatus.REPORT_READY, OrderStatus.COMPLETED]),
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
        .first()
    )
    if not order:
        return None
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "order_status": OrderStatus.REPORT_READY.value,
    }


def get_participant_by_member_id(
    db: Session,
    member_id: int
) -> Optional[GeneticTestParticipant]:
    """Get participant record by member ID."""
    return db.query(GeneticTestParticipant).filter(
        GeneticTestParticipant.member_id == member_id
    ).first()


def get_participant_by_mobile(
    db: Session,
    mobile: str
) -> Optional[GeneticTestParticipant]:
    """Get participant record by mobile number."""
    return db.query(GeneticTestParticipant).filter(
        GeneticTestParticipant.mobile == mobile
    ).first()


def get_all_participants(
    db: Session,
    has_taken_test: Optional[bool] = None,
    plan_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[GeneticTestParticipant]:
    """Get all participants with optional filters."""
    query = db.query(GeneticTestParticipant)
    
    if has_taken_test is not None:
        query = query.filter(GeneticTestParticipant.has_taken_genetic_test == has_taken_test)
    
    if plan_type:
        query = query.filter(GeneticTestParticipant.plan_type == plan_type)
    
    return query.order_by(GeneticTestParticipant.created_at.desc()).offset(offset).limit(limit).all()


def create_or_update_participant(
    db: Session,
    user_id: int,
    member_id: int,
    mobile: str,
    name: str,
    plan_type: Optional[str] = None,
    product_id: Optional[int] = None,
    category_id: Optional[int] = None,
    order_id: Optional[int] = None,
    has_taken_genetic_test: bool = True
) -> GeneticTestParticipant:
    """
    Create or update participant record.
    If participant exists, updates the record. Otherwise creates new one.
    """
    # Check if participant already exists for this member
    participant = get_participant_by_member_id(db, member_id)
    
    if participant:
        # Update existing record
        participant.user_id = user_id
        participant.mobile = mobile  # Update mobile (denormalized)
        participant.name = name  # Update name (denormalized)
        participant.has_taken_genetic_test = has_taken_genetic_test
        participant.plan_type = plan_type  # Update to latest plan type
        participant.product_id = product_id
        participant.category_id = category_id
        participant.order_id = order_id  # Update to latest order
        logger.info(f"Updated genetic test participant record for member {member_id}")
    else:
        # Create new record
        participant = GeneticTestParticipant(
            user_id=user_id,
            member_id=member_id,
            mobile=mobile,
            name=name,
            has_taken_genetic_test=has_taken_genetic_test,
            plan_type=plan_type,
            product_id=product_id,
            category_id=category_id,
            order_id=order_id
        )
        db.add(participant)
        logger.info(f"Created new genetic test participant record for member {member_id}")
    
    db.flush()
    return participant


def check_if_member_has_taken_test(
    db: Session,
    member_id: Optional[int] = None,
    mobile: Optional[str] = None
) -> bool:
    """
    Check if a member has taken genetic test.
    Can query by member_id or mobile.
    """
    if member_id:
        participant = get_participant_by_member_id(db, member_id)
        return participant.has_taken_genetic_test if participant else False
    
    if mobile:
        participant = get_participant_by_mobile(db, mobile)
        return participant.has_taken_genetic_test if participant else False
    
    return False


def get_participant_info(
    db: Session,
    member_id: Optional[int] = None,
    mobile: Optional[str] = None
) -> Optional[dict]:
    """
    Get participant information including test status and plan type.
    Returns dict with has_taken_test and plan_type.
    """
    participant = None
    
    if member_id:
        participant = get_participant_by_member_id(db, member_id)
    elif mobile:
        participant = get_participant_by_mobile(db, mobile)
    
    if participant:
        return {
            "member_id": participant.member_id,
            "mobile": participant.mobile,
            "name": participant.name,
            "has_taken_genetic_test": participant.has_taken_genetic_test,
            "plan_type": participant.plan_type,
            "product_id": participant.product_id,
            "order_id": participant.order_id
        }
    
    return None

