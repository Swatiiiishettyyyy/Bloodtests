"""
Tracking Model - Database schema for tracking_records table
"""
from sqlalchemy import Column, String, Boolean, DECIMAL, Float, Text, DateTime, func, Index
from sqlalchemy.dialects.postgresql import UUID, INET
from database import Base
from Login_module.Utils.datetime_utils import now_ist
import uuid


class TrackingRecord(Base):
    """
    Tracking records table for location and analytics data with consent management.
    All fields default to NULL except consent flags (default FALSE) and timestamps (auto-generated).
    """
    __tablename__ = "tracking_records"

    # Primary Key
    record_id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )

    # User Identity (NULL for anonymous)
    user_id = Column(String(255), nullable=True, index=True)  # User ID for authenticated users (stored as-is, not hashed)
    ga_client_id = Column(String(255), nullable=True, index=True)  # Google Analytics Client ID
    session_id = Column(String(255), nullable=True, index=True)

    # Consent Flags (DEFAULT FALSE)
    ga_consent = Column(Boolean, default=False, nullable=False)
    location_consent = Column(Boolean, default=False, nullable=False)

    # Location Data (NULL by default)
    latitude = Column(DECIMAL(10, 8), nullable=True)
    longitude = Column(DECIMAL(11, 8), nullable=True)
    accuracy = Column(Float, nullable=True)

    # Page Context (NULL by default)
    page_url = Column(String(500), nullable=True)
    referrer = Column(String(500), nullable=True)

    # Device Information (NULL by default)
    user_agent = Column(Text, nullable=True)
    device_type = Column(String(50), nullable=True)
    browser = Column(String(100), nullable=True)
    operating_system = Column(String(100), nullable=True)
    language = Column(String(20), nullable=True)
    timezone = Column(String(100), nullable=True)

    # Network Information
    ip_address = Column(String(45), nullable=True)  # Using String to support both IPv4 and IPv6

    # Metadata
    record_type = Column(String(50), nullable=True)  # 'consent_update', 'location_update', 'page_view'

    # Timestamps (ALWAYS populated)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    consent_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Indexes for performance
    __table_args__ = (
        Index('idx_user_id', 'user_id'),
        Index('idx_ga_client_id', 'ga_client_id'),
        Index('idx_session_id', 'session_id'),
        Index('idx_created_at', 'created_at'),
        Index('idx_consent_flags', 'ga_consent', 'location_consent'),
    )

