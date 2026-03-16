from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Index, Text
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class ConsentProduct(Base):
    __tablename__ = "consent_products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    # Relationship
    consents = relationship("UserConsent", back_populates="consent_product")


class UserConsent(Base):
    __tablename__ = "user_consents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_phone = Column(String(100), nullable=False, index=True)  # Increased to 100 for encrypted phone numbers
    member_id = Column(Integer, ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("consent_products.id", ondelete="CASCADE"), nullable=False, index=True)
    product = Column(String(100), nullable=True)  # Product name (denormalized for quick access)
    consent_given = Column(Integer, nullable=False, default=1)  # 1 for yes, only store yes consents
    consent_source = Column(String(20), nullable=False)  # "login" or "product"
    status = Column(String(10), nullable=False, default="yes")  # "yes" or "no"
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    member = relationship("Member", foreign_keys=[member_id])
    consent_product = relationship("ConsentProduct", foreign_keys=[product_id], back_populates="consents")

    # Composite index to prevent duplicates (member-scoped)
    __table_args__ = (
        Index('idx_member_product', 'member_id', 'product_id', unique=True),
    )


class PartnerConsent(Base):
    """
    Partner consent table for Product 11 (Child simulator).
    Stores dual consent records where both user and partner must consent.
    """
    __tablename__ = "partner_consents"

    id = Column(Integer, primary_key=True, index=True)
    
    # Product info (should always be product_id = 11)
    product_id = Column(Integer, ForeignKey("consent_products.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # User (requester) info - who initiated the consent
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_member_id = Column(Integer, ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_name = Column(String(100), nullable=False)
    user_mobile = Column(String(100), nullable=False, index=True)  # Increased to 100 for encrypted phone numbers
    user_consent = Column(String(10), nullable=False, default="no")  # "yes" or "no"
    
    # Partner info - the spouse/partner who needs to consent
    partner_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)  # Partner's user_id if they have account
    partner_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True)  # Partner's member_id if exists
    partner_name = Column(String(100), nullable=True)  # Partner's name
    partner_mobile = Column(String(100), nullable=False, index=True)  # Partner's mobile (required for OTP) - Increased to 100 for encrypted phone numbers
    partner_consent = Column(String(10), nullable=False, default="no")  # "yes" or "no"
    
    # Final consent status (only "yes" if both user and partner consented)
    final_status = Column(String(10), nullable=False, default="no")  # "yes" only if both said yes
    
    # Request state machine fields (added in migration 038)
    # Status flow: PENDING_REQUEST -> OTP_SENT -> CONSENT_GIVEN (OTP verification automatically grants consent)
    # OTP_VERIFIED is deprecated but kept for backward compatibility with old records
    request_status = Column(String(20), nullable=False, default="PENDING_REQUEST")  # PENDING_REQUEST, OTP_SENT, OTP_VERIFIED (deprecated), CONSENT_GIVEN, DECLINED, EXPIRED, CANCELLED, REVOKED_BY_PARTNER
    request_id = Column(String(50), nullable=True, unique=True, index=True)  # Unique request identifier for OTP flow
    
    # OTP tracking fields
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)  # When OTP expires
    otp_sent_at = Column(DateTime(timezone=True), nullable=True)  # When OTP was sent
    
    # Request expiration fields
    request_expires_at = Column(DateTime(timezone=True), nullable=True)  # When the entire request expires
    last_request_created_at = Column(DateTime(timezone=True), nullable=True)  # Last time a request was created (for cooldown)
    
    # Rate limiting fields
    failed_attempts = Column(Integer, nullable=False, default=0)  # Number of failed OTP verification attempts
    resend_count = Column(Integer, nullable=False, default=0)  # Number of times OTP was resent
    total_attempts = Column(Integer, nullable=False, default=1)  # Total number of requests for this consent
    
    # Revocation tracking
    revoked_at = Column(DateTime(timezone=True), nullable=True)  # When consent was revoked by partner
    
    # Metadata
    consent_source = Column(String(20), nullable=False, default="product")  # "product" or "partner_otp"
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist, nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    user_member = relationship("Member", foreign_keys=[user_member_id])
    partner_user = relationship("User", foreign_keys=[partner_user_id])
    partner_member = relationship("Member", foreign_keys=[partner_member_id])
    consent_product = relationship("ConsentProduct", foreign_keys=[product_id])
    
    # Composite index to prevent duplicates (one consent record per user_member_id for product 11)
    __table_args__ = (
        Index('idx_user_member_product', 'user_member_id', 'product_id', unique=True),
    )
