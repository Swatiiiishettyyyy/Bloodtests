# app/models/address.py
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, func
from database import Base
from Login_module.Utils.datetime_utils import now_ist

class Address(Base):
    __tablename__ = "addresses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Removed: first_name, last_name, email, mobile (as per requirements)

    address_label = Column(String(50), nullable=False)
    street_address = Column(String(255), nullable=False)
    landmark = Column(String(255), nullable=True)
    locality = Column(String(150), nullable=False)
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    postal_code = Column(String(20), nullable=False, index=True)  # Pincode - required
    country = Column(String(100), nullable=False, default="India")

    save_for_future = Column(Boolean, default=True)
    
    # Soft delete fields
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)


class ServiceableLocation(Base):
    __tablename__ = "serviceable_locations"

    id = Column(Integer, primary_key=True, index=True)
    location = Column(String(150), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=now_ist)