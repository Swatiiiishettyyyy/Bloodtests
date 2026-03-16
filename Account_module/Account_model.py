"""
Account feedback models - store user reasons for phone number change
and account deletion so support can review and act manually.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from database import Base
from Login_module.Utils.datetime_utils import now_ist


class AccountFeedbackRequest(Base):
    """
    Stores feedback/requests from users about phone number change and account deletion.

    Each row is one request of a specific type for a user/self-member.
    """

    __tablename__ = "account_feedback_requests"

    id = Column(Integer, primary_key=True, index=True)

    # Who submitted the request
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True)
    member_name = Column(String(255), nullable=True)
    current_phone = Column(String(50), nullable=True)  # Current phone for context
    new_phone = Column(String(50), nullable=True)  # New phone number (only for PHONE_CHANGE requests)

    # What kind of request this is
    # Expected values: "PHONE_CHANGE", "ACCOUNT_DELETION"
    request_type = Column(String(50), nullable=False, index=True)

    # User-provided reason text
    reason = Column(Text, nullable=False)

    # When the request was submitted
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)

    # Relationships (optional, for convenience)
    user = relationship("User")
    member = relationship("Member")


