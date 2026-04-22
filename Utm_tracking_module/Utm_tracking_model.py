"""
UTM tracking model — stores landing URL and UTM parameters; optional user linkage after signup.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func

from database import Base


class UtmTracking(Base):
    __tablename__ = "utm_tracking"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    fingerprint = Column(String(255), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    phone = Column(String(100), nullable=True)
    utm_source = Column(String(255), nullable=True)
    utm_medium = Column(String(255), nullable=True)
    utm_campaign = Column(String(255), nullable=True)
    utm_term = Column(String(255), nullable=True)
    utm_content = Column(String(255), nullable=True)
    landing_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
