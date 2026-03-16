from sqlalchemy import Column, Integer, String, DateTime, JSON, func
from sqlalchemy.orm import Session
from database import Base
from typing import Optional
import json
from Login_module.Utils.datetime_utils import now_ist


class ProfileAuditLog(Base):
    __tablename__ = "profile_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    action = Column(String(50), nullable=False)  # EDIT / VIEW / etc.
    old_data = Column(JSON, nullable=True)
    new_data = Column(JSON, nullable=True)
    ip_address = Column(String(50), nullable=True, index=True)
    user_agent = Column(String(500), nullable=True)
    correlation_id = Column(String(100), nullable=True, index=True)  # For request tracing
    timestamp = Column(DateTime(timezone=True), default=now_ist, index=True)


def log_profile_update(
    db: Session,
    user_id: int,
    old_data: dict,
    new_data: dict,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Store profile update audit log.
    """
    log_entry = ProfileAuditLog(
        user_id=user_id,
        action="PROFILE_UPDATE",
        old_data=old_data,
        new_data=new_data,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id
    )
    
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    return log_entry

