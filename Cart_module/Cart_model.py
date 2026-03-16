from sqlalchemy import Column, Integer, ForeignKey, DateTime, func, String, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist


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
    cart_id = Column(Integer, ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True)  # Foreign key to cart table
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # Keep for validation/security
    product_id = Column(Integer, ForeignKey("products.ProductId"), nullable=False, index=True)
    address_id = Column(Integer, ForeignKey("addresses.id"), nullable=False, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=1)
    
    # For couple/family products: link multiple cart items together
    # All items for the same product purchase share the same group_id
    group_id = Column(String(100), nullable=False, index=True)  # UUID or timestamp-based ID
    
    # Soft delete flag
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)  # True if cart item is deleted/cleared
    
    # Note: Coupon tracking is handled by cart_coupons table, not in cart_items
    # This keeps cart items clean and coupons are managed at cart level
    
    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    # Relationships
    cart = relationship("Cart", back_populates="items")
    user = relationship("User")
    product = relationship("Product")
    address = relationship("Address")
    member = relationship("Member")