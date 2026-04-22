"""
Thyrocare cart service — helpers for cart-related operations.
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_thyrocare_confirmed_amount(db: Session, cart_items: list) -> Optional[float]:
    """
    Legacy helper kept for backward compatibility.

    IMPORTANT: We no longer query Thyrocare cart/price-breakup to derive net payable.
    This function now returns the **catalog subtotal** for blood-test cart items:
    sum(selling_price per member) across all blood-test groups.
    """
    from Cart_module.Cart_model import ProductType
    from .Thyrocare_model import ThyrocareProduct

    total = 0.0
    found = False

    for item in cart_items:
        if getattr(item, "product_type", None) != ProductType.BLOOD_TEST:
            continue

        found = True
        product = getattr(item, "thyrocare_product", None)
        if not product and getattr(item, "thyrocare_product_id", None):
            product = (
                db.query(ThyrocareProduct)
                .filter(ThyrocareProduct.id == item.thyrocare_product_id)
                .first()
            )
        if not product:
            raise RuntimeError(f"ThyrocareProduct not found for cart item '{getattr(item, 'id', None)}'")

        total += float(product.selling_price or 0)

    return total if found else None
