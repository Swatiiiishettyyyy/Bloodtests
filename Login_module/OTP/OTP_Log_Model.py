from sqlalchemy import Column, Integer, String, DateTime, func, Text, ForeignKey
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class OTPAuditLog(Base):
    """
    OTP audit log - tracks events only, no OTP values stored.
    Event types: GENERATED, VERIFIED, FAILED, BLOCKED
    """
    __tablename__ = "otp_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    device_id = Column(String(255), nullable=True, index=True)
    event_type = Column(String(20), nullable=False, index=True)  # GENERATED, VERIFIED, FAILED, BLOCKED
    timestamp = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    reason = Column(Text, nullable=True)  # Optional reason or failed attempt count
    phone_number = Column(String(30), nullable=True, index=True)  # For backward compatibility
    ip_address = Column(String(50), nullable=True, index=True)  # IP address of request
    user_agent = Column(String(500), nullable=True)  # User agent/browser info
    correlation_id = Column(String(100), nullable=True, index=True)  # For request tracing
    
    