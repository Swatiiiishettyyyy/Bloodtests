"""
Genetic Test Participant model - tracks users who have taken genetic tests.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func, Boolean
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class GeneticTestParticipant(Base):
    """
    Tracks participants who have taken genetic tests.
    Stores member information with test completion status and plan type.
    """
    __tablename__ = "genetic_test_participants"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True)
    
    # Denormalized fields for quick lookup (mobile and name can change, but we store them here for historical tracking)
    mobile = Column(String(20), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    
    # Test completion flag
    has_taken_genetic_test = Column(Boolean, nullable=False, default=False, index=True)
    
    # Plan information
    plan_type = Column(String(50), nullable=True, index=True)  # "single", "couple", "family"
    
    # Product and order references (optional, for tracking which test/order)
    product_id = Column(Integer, ForeignKey("products.ProductId", ondelete="SET NULL"), nullable=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist, nullable=True)
    
    # Relationships
    user = relationship("User")
    member = relationship("Member")
    product = relationship("Product", foreign_keys=[product_id])
    order = relationship("Order", foreign_keys=[order_id])

