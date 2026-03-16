from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, func, Index
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class NewsletterSubscription(Base):
    __tablename__ = "newsletter_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    subscribed_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
    unsubscribed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Unique constraint on email to prevent duplicates
    __table_args__ = (
        Index('idx_email_active', 'email', 'is_active'),
    )
    
    # Relationship
    # user = relationship("User", backref="newsletter_subscriptions")

