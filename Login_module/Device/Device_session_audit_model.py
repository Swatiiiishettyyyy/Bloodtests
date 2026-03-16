"""
Session audit log model for tracking session creation, deletion, and activity.
"""
from sqlalchemy import Column, Integer, String, DateTime, func, Text, ForeignKey
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class SessionAuditLog(Base):
    """
    Session audit log - tracks session lifecycle events.
    Event types: CREATED, DELETED, EXPIRED, ACTIVITY
    """
    __tablename__ = "session_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id = Column(Integer, ForeignKey("device_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    device_id = Column(String(255), nullable=True, index=True)
    event_type = Column(String(20), nullable=False, index=True)  # CREATED, DELETED, EXPIRED, ACTIVITY
    timestamp = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    reason = Column(Text, nullable=True)  # Optional reason
    ip_address = Column(String(50), nullable=True, index=True)
    user_agent = Column(String(500), nullable=True)
    correlation_id = Column(String(100), nullable=True, index=True)  # For request tracing

