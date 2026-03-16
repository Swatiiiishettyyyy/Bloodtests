"""
Member audit log model for tracking member CRUD operations.
"""
from sqlalchemy import Column, Integer, String, DateTime, func, JSON, ForeignKey
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class MemberAuditLog(Base):
    """
    Member audit log - tracks member lifecycle events.
    Event types: CREATED, UPDATED, DELETED
    """
    __tablename__ = "member_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True)
    member_name = Column(String(255), nullable=True, index=True)  # Member name for clear identification
    member_identifier = Column(String(100), nullable=True)  # Composite identifier: name + mobile for unique identification
    event_type = Column(String(20), nullable=False, index=True)  # CREATED, UPDATED, DELETED
    timestamp = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    old_data = Column(JSON, nullable=True)  # Previous member data before update (includes member_id)
    new_data = Column(JSON, nullable=True)  # New member data after update (includes member_id)
    reason = Column(String(500), nullable=True)  # Optional reason
    ip_address = Column(String(50), nullable=True, index=True)
    user_agent = Column(String(500), nullable=True)
    correlation_id = Column(String(100), nullable=True, index=True)  # For request tracing

