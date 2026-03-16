from sqlalchemy import Column, Integer, String, DateTime, Boolean, func, JSON
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class Banner(Base):
    __tablename__ = "banners"

    id = Column(Integer, primary_key=True, index=True)
    
    # Content fields
    title = Column(String(200), nullable=True)
    subtitle = Column(String(500), nullable=True)
    image_url = Column(String(500), nullable=False)  # S3 URL for banner image
    
    # Action field - stored as JSON: {"type": "GENETIC_TEST", "value": "CANCER_PREDISPOSITION"} or {"type": "GENETIC_TEST"}
    action = Column(JSON, nullable=True)  # Can have just type or type+value
    
    # Display control
    position = Column(Integer, nullable=False, default=0, index=True)  # Display order
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    
    # Date scheduling
    start_date = Column(DateTime(timezone=True), nullable=True, index=True)
    end_date = Column(DateTime(timezone=True), nullable=True, index=True)
    
    # Soft delete
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist, nullable=True)

