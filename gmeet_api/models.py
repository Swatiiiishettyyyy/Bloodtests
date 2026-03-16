"""
Database models for Google Meet API.
All tables use the prefix 'counsellor_gmeet_'.
"""
import sys
from pathlib import Path
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from Login_module.Utils.datetime_utils import now_ist

# Add parent directory to path to import shared database
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    # Try importing from parent database module
    from database import Base
except ImportError:
    # Fallback to local database (shouldn't happen in integrated setup)
    try:
        from .database import Base
    except ImportError:
        from database import Base


class CounsellorToken(Base):
    """
    Stores Google OAuth tokens for each counsellor.
    Tokens should be encrypted in production.
    """
    __tablename__ = "counsellor_gmeet_tokens"

    id = Column(Integer, primary_key=True, index=True)
    counsellor_id = Column(String(255), unique=True, nullable=False, index=True)
    access_token = Column(Text, nullable=False)  # Encrypt in production
    refresh_token = Column(Text, nullable=True)
    token_uri = Column(String(500), nullable=True)
    client_id = Column(String(500), nullable=True)
    client_secret = Column(String(500), nullable=True)
    scopes = Column(JSON, nullable=True)  # Store as JSON array
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)
    is_active = Column(Boolean, default=True)

    # Relationships
    bookings = relationship("CounsellorBooking", back_populates="counsellor_token")


class CounsellorBooking(Base):
    """
    Stores all booking information and activity.
    """
    __tablename__ = "counsellor_gmeet_bookings"

    id = Column(Integer, primary_key=True, index=True)
    counsellor_id = Column(String(255), nullable=False, index=True)
    counsellor_member_id = Column(String(255), nullable=False, index=True)
    counsellor_token_id = Column(Integer, ForeignKey("counsellor_gmeet_tokens.id"), nullable=True)
    patient_name = Column(String(255), nullable=False)
    patient_email = Column(String(255), nullable=True, index=True)
    patient_phone = Column(String(20), nullable=True, index=True)
    start_time = Column(DateTime(timezone=True), nullable=False, index=True)
    end_time = Column(DateTime(timezone=True), nullable=False)
    google_event_id = Column(String(255), unique=True, nullable=True, index=True)
    meet_link = Column(Text, nullable=True)
    calendar_link = Column(Text, nullable=True)
    status = Column(String(50), default="confirmed", index=True)  # confirmed, cancelled, completed
    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)

    # Relationships
    counsellor_token = relationship("CounsellorToken", back_populates="bookings")
    logs = relationship("CounsellorActivityLog", back_populates="booking")


class CounsellorActivityLog(Base):
    """
    Logs all API activity and events for audit purposes.
    """
    __tablename__ = "counsellor_gmeet_activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("counsellor_gmeet_bookings.id"), nullable=True)
    counsellor_id = Column(String(255), nullable=False, index=True)
    activity_type = Column(String(100), nullable=False, index=True)  # availability_check, booking_created, booking_cancelled, error
    endpoint = Column(String(255), nullable=True)
    request_data = Column(JSON, nullable=True)
    response_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_ist, index=True)

    # Relationships
    booking = relationship("CounsellorBooking", back_populates="logs")


class CounsellorGmeetList(Base):
    """
    Stores counsellor information from Google OAuth signup.
    Contains auto-generated unique 6-character counsellor_id.
    Stores all available information from Google userinfo endpoint.
    """
    __tablename__ = "counsellor_gmeet_list"

    id = Column(Integer, primary_key=True, index=True)
    counsellor_id = Column(String(6), unique=True, nullable=False, index=True)  # Auto-generated 6-char ID
    google_user_id = Column(String(255), unique=True, nullable=False, index=True)  # Google's 'id' or 'sub' field
    email = Column(String(255), unique=True, nullable=False, index=True)  # Google email
    email_verified = Column(Boolean, nullable=True)  # Whether Google email is verified
    name = Column(String(255), nullable=True)  # Full name from Google
    given_name = Column(String(255), nullable=True)  # First name from Google
    family_name = Column(String(255), nullable=True)  # Last name from Google
    profile_picture_url = Column(String(500), nullable=True)  # Google profile picture URL
    locale = Column(String(10), nullable=True)  # User's language/region preference (e.g., "en", "en-IN")
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)

