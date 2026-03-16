from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, func, Text
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist

class DeviceSession(Base):
    __tablename__ = "device_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_token = Column(String(255), nullable=False, index=True, unique=True)  # session token (JWT or random)
    device_id = Column(String(255), nullable=True, index=True)                # device id from client
    device_platform = Column(String(50), nullable=True)           # web / mobile / ios / android etc
    ip_address = Column(String(50), nullable=True)
    browser_info = Column(Text, nullable=True)                    # browser/user agent info
    last_active = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    event_on_logout = Column(DateTime(timezone=True), nullable=True)  # timestamp when logged out
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Dual Token Strategy: Link to refresh token family
    refresh_token_family_id = Column(String(36), nullable=True, index=True)  # UUID linking to current token family
    
    # Legacy fields for backward compatibility (can be removed later)
    session_key = Column(String(255), nullable=True, index=True)  # deprecated, use session_token
    device_details = Column(String(500), nullable=True)           # deprecated, use browser_info
    user_agent = Column(String(500), nullable=True)                # deprecated, use browser_info
    expires_at = Column(DateTime(timezone=True), nullable=True)    # deprecated, tokens expire via JWT

    # relationships - optional
    # user = relationship("User", backref="sessions")