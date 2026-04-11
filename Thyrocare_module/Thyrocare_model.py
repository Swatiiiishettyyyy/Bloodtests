"""
Thyrocare product catalogue models.
Stores blood test products and their parameters locally.
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class ThyrocareProduct(Base):
    __tablename__ = "thyrocare_products"

    id = Column(Integer, primary_key=True, index=True)
    thyrocare_id = Column(String(50), unique=True, nullable=False, index=True)  # e.g. "P1524"
    name = Column(String(300), nullable=False, index=True)
    type = Column(String(50), nullable=False)  # e.g. "SSKU"
    no_of_tests_included = Column(Integer, nullable=False, default=0)

    # Pricing (stored from catalogue; can be updated via sync)
    listing_price = Column(Float, nullable=False, default=0.0)
    selling_price = Column(Float, nullable=False, default=0.0)
    discount_percentage = Column(Float, nullable=False, default=0.0)
    notational_incentive = Column(Float, nullable=False, default=0.0)

    # Beneficiary limits
    beneficiaries_min = Column(Integer, nullable=False, default=1)
    beneficiaries_max = Column(Integer, nullable=False, default=1)
    beneficiaries_multiple = Column(Integer, nullable=False, default=1)

    # Flags
    is_fasting_required = Column(Boolean, nullable=True)
    is_home_collectible = Column(Boolean, nullable=True)

    # Category
    category = Column(String(200), nullable=True, index=True)

    # Content (manually filled)
    about = Column(Text, nullable=True)
    short_description = Column(String(500), nullable=True)

    # Soft delete / active
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    # Relationships
    parameters = relationship(
        "ThyrocareTestParameter",
        back_populates="product",
        cascade="all, delete-orphan"
    )


class ThyrocareTestParameter(Base):
    """One row per test parameter included in a ThyrocareProduct."""
    __tablename__ = "thyrocare_test_parameters"

    id = Column(Integer, primary_key=True, index=True)
    thyrocare_product_id = Column(
        Integer,
        ForeignKey("thyrocare_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    name = Column(String(300), nullable=False)
    group_name = Column(String(200), nullable=True)

    product = relationship("ThyrocareProduct", back_populates="parameters")


class ThyrocarePincode(Base):
    """Serviceable pincodes fetched from Thyrocare serviceability API."""
    __tablename__ = "thyrocare_pincodes"

    id = Column(Integer, primary_key=True, index=True)
    pincode = Column(String(10), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    synced_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
