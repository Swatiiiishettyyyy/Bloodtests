"""
Phone Change Request Models
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, JSON, func, Index
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist
import enum


class PhoneChangeStatus(str, enum.Enum):
    """Phone change request status"""
    OLD_NUMBER_PENDING = "old_number_pending"
    OLD_NUMBER_VERIFIED = "old_number_verified"
    NEW_NUMBER_PENDING = "new_number_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED_OLD_OTP = "failed_old_otp"
    FAILED_NEW_OTP = "failed_new_otp"
    LOCKED = "locked"
    FAILED_DB_UPDATE = "failed_db_update"
    FAILED_SMS = "failed_sms"


class PhoneChangeRequest(Base):
    """
    Phone change request table - tracks phone number change process
    """
    __tablename__ = "phone_change_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Phone numbers
    old_phone = Column(String(20), nullable=False)  # Note: Stored as plaintext for OTP, but can be up to 20 chars
    new_phone = Column(String(20), nullable=True)  # Set when new number verification starts - Note: Stored as plaintext for OTP
    
    # Status tracking
    status = Column(String(50), nullable=False, default=PhoneChangeStatus.OLD_NUMBER_PENDING.value, index=True)
    
    # Session token for linking Step 1 and Step 2
    session_token = Column(String(100), nullable=True, unique=True, index=True)
    
    # OTP attempt tracking
    old_phone_otp_attempts = Column(Integer, default=0, nullable=False)
    new_phone_otp_attempts = Column(Integer, default=0, nullable=False)
    
    # SMS retry tracking
    sms_retry_count = Column(Integer, default=0, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)  # Overall request expiry
    old_phone_verified_at = Column(DateTime(timezone=True), nullable=True)  # When old number was verified
    new_phone_verified_at = Column(DateTime(timezone=True), nullable=True)  # When new number was verified
    completed_at = Column(DateTime(timezone=True), nullable=True)  # When process completed
    cooldown_until = Column(DateTime(timezone=True), nullable=True, index=True)  # Lock expiry time
    
    # IP address for audit
    ip_address = Column(String(50), nullable=True, index=True)
    
    # Relationships
    user = relationship("User", backref="phone_change_requests")
    
    # Index for active requests per user
    __table_args__ = (
        Index('idx_user_status_active', 'user_id', 'status', 'created_at'),
    )


class PhoneChangeAuditLog(Base):
    """
    Phone change audit log - tracks all actions for audit trail
    """
    __tablename__ = "phone_change_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    request_id = Column(Integer, ForeignKey("phone_change_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Action details
    action = Column(String(100), nullable=False, index=True)  # e.g., "verify_old_initiated", "otp_sent", "otp_verified", "db_updated"
    status = Column(String(50), nullable=False, index=True)  # Current status of request at time of action
    
    # Additional details stored as JSON
    details = Column(JSON, nullable=True)  # Store phone numbers, error messages, etc.
    
    # Request metadata
    ip_address = Column(String(50), nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    
    # Success/failure tracking
    success = Column(Integer, nullable=False, default=1)  # 1 for success, 0 for failure
    error_message = Column(Text, nullable=True)  # Error message if action failed
    
    # Relationships
    user = relationship("User", backref="phone_change_audit_logs")
    request = relationship("PhoneChangeRequest", backref="audit_logs")

