from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from database import Base
from Login_module.Utils.datetime_utils import now_ist

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    mobile = Column(String(100), nullable=False, index=True)  # Increased to 100 for encrypted phone numbers (unique constraint removed)
    profile_photo_url = Column(String(500), nullable=True)  # URL/path to profile photo
    created_at = Column(DateTime(timezone=True), default=now_ist)
    is_active = Column(Boolean, default=True)
    notifications_enabled = Column(Boolean, default=True, nullable=False)
    # additional fields can be added (profile, role, etc.)