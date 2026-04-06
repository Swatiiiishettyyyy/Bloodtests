from sqlalchemy import Column, Integer, ForeignKey, DateTime, func, String, Boolean, UniqueConstraint, Enum, Date, Time
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist
import enum


class ProductType(str, enum.Enum):
    GENETIC = "genetic"
    BLOOD_TEST = "blood_test"


class Cart(Base):
    """
    Cart table - one active cart per user.
    Tracks the user's shopping cart container.
    """
    __tablename__ = "carts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Active flag - allows for future multi-cart support
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)  # Last time any item was added/removed
    
    # Relationships
    user = relationship("User")
    items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")
    
    # Note: Unique constraint for one active cart per user is enforced by application logic
    # Database-level partial unique indexes are database-specific and may not work in all cases
    # The get_or_create_user_cart() function ensures only one active cart per user


class CartItem(Base):
    __tablename__ = "cart_items"

    id = Column(Integer, primary_key=True, index=True)
    cart_id = Column(Integer, ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Product type — determines which FK is set
    product_type = Column(String(20), nullable=False, default=ProductType.GENETIC.value, index=True)

    # Genetic test product (nullable — only set when product_type = GENETIC)
    product_id = Column(Integer, ForeignKey("products.ProductId"), nullable=True, index=True)

    # Blood test product (nullable — only set when product_type = BLOOD_TEST)
    thyrocare_product_id = Column(Integer, ForeignKey("thyrocare_products.id"), nullable=True, index=True)

    address_id = Column(Integer, ForeignKey("addresses.id"), nullable=False, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=1)

    # For couple/family/multi-beneficiary products: link multiple cart items together
    group_id = Column(String(100), nullable=False, index=True)

    # Blood test appointment (only set when product_type = BLOOD_TEST)
    appointment_date = Column(Date, nullable=True)
    appointment_start_time = Column(String(20), nullable=True)  # e.g. "09:00"

    # Soft delete flag
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    # Relationships
    cart = relationship("Cart", back_populates="items")
    user = relationship("User")
    product = relationship("Product")
    thyrocare_product = relationship("ThyrocareProduct")
    address = relationship("Address")
    member = relationship("Member")