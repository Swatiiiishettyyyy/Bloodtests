
from sqlalchemy import Column, Integer, String, DateTime, func, JSON, Text
from database import Base
from Login_module.Utils.datetime_utils import now_ist

class AddressAudit(Base):
    __tablename__ = "address_audit"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    phone_number = Column(String(20), nullable=True)
    address_id = Column(Integer, nullable=True, index=True)  # Made nullable for deletions
    address_label = Column(String(255), nullable=True, index=True)  # Address label for clear identification
    address_identifier = Column(String(200), nullable=True)  # Composite identifier: label + city + pincode
    action = Column(String(50), nullable=False)  # created / updated / deleted
    old_data = Column(JSON, nullable=True)  # Previous address data before update (includes address_id)
    new_data = Column(JSON, nullable=True)  # New address data after update (includes address_id)
    ip_address = Column(String(50), nullable=True, index=True)
    user_agent = Column(String(500), nullable=True)
    correlation_id = Column(String(100), nullable=True, index=True)  # For request tracing
    created_at = Column(DateTime(timezone=True), default=now_ist, index=True)