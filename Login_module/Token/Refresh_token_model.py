"""
Refresh Token Model - Stores refresh tokens with token family tracking for rotation detection.
"""
from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, func, Text
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist
import uuid


class RefreshToken(Base):
    """
    Refresh Token table - Stores refresh token hashes with token family tracking.
    Token family (token_family_id) groups related refresh tokens to detect reuse/theft.
    """
    __tablename__ = "refresh_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("device_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    token_family_id = Column(String(36), nullable=False, index=True)  # UUID for token family tracking
    token_hash = Column(String(64), nullable=False, index=True)  # SHA256 hash of refresh token
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    is_revoked = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    ip_address = Column(String(50), nullable=True, index=True)
    user_agent = Column(Text, nullable=True)
    
    # Relationships
    # user = relationship("User", backref="refresh_tokens")
    # session = relationship("DeviceSession", backref="refresh_tokens")

