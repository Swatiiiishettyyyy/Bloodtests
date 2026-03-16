from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func, Date, Integer as IntCol, Boolean
from sqlalchemy.orm import relationship
from database import Base
from Product_module.Product_model import Category
from Login_module.Utils.datetime_utils import now_ist


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Per-member API key for identifying member in external requests
    # Generated once at member creation time, immutable thereafter
    api_key = Column(String(128), nullable=True, unique=True, index=True)

    name = Column(String(100), nullable=False)
    relation = Column(String(50), nullable=False)  # Accepts any relation string value - no enum restriction
    
    # Required fields: age, gender, dob, mobile
    age = Column(IntCol, nullable=False)
    gender = Column(String(20), nullable=False)  # M, F, Other
    dob = Column(Date, nullable=False)
    mobile = Column(String(100), nullable=False)  # Increased to 100 for encrypted phone numbers
    email = Column(String(255), nullable=True)  # Optional email address
    
    # Track which category/plan this member is associated with
    # This helps prevent duplicate entries in same category
    associated_category = Column(String(100), nullable=True, index=True)  # "genome_testing"
    associated_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    associated_plan_type = Column(String(50), nullable=True, index=True)  # "single", "couple", "family"
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    is_self_profile = Column(Boolean, nullable=False, default=False, index=True)  # True if this is the user's own profile
    
    # Profile photo
    profile_photo_url = Column(String(500), nullable=True)  # URL/path to member's profile photo

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    category = relationship(Category, lazy="joined", foreign_keys=[associated_category_id])