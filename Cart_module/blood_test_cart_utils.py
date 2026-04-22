"""Blood-test cart invariants: one active group per user + Thyrocare product."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from .Cart_model import CartItem, ProductType

_MIN_TS = datetime.min.replace(tzinfo=timezone.utc)


def retire_superseded_blood_test_lines(
    db: Session, user_id: int, thyrocare_product_id: int
) -> None:
    """Soft-delete all active blood-test rows for this user and product (any group_id)."""
    db.query(CartItem).filter(
        CartItem.user_id == user_id,
        CartItem.thyrocare_product_id == thyrocare_product_id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.is_deleted == False,
    ).update({"is_deleted": True}, synchronize_session=False)


def _is_blood_test_item(item: CartItem) -> bool:
    pt = getattr(item, "product_type", None)
    if pt == ProductType.BLOOD_TEST:
        return True
    return str(pt).lower() == "blood_test"


def filter_latest_blood_test_group_per_product(cart_items: List[CartItem]) -> List[CartItem]:
    """
    For /cart/view: if legacy data has multiple active groups for the same Thyrocare product,
    keep only the newest group (by max row created_at within the group). Other line types unchanged.
    """
    if not cart_items:
        return cart_items

    blood_by_product: dict = defaultdict(list)
    for item in cart_items:
        if _is_blood_test_item(item):
            blood_by_product[item.thyrocare_product_id].append(item)

    if not blood_by_product:
        return cart_items

    drop_ids = set()
    for pid, items in blood_by_product.items():
        if pid is None:
            continue
        by_group: dict = defaultdict(list)
        for i in items:
            gid = i.group_id or f"single_{i.id}"
            by_group[gid].append(i)
        def _row_ts(i: CartItem):
            ts = i.created_at
            if ts is None:
                return _MIN_TS
            # Normalise naive datetimes (legacy rows) to UTC-aware so comparison doesn't raise TypeError
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts

        best_gid = max(by_group.keys(), key=lambda g: max(_row_ts(i) for i in by_group[g]))
        for g, grp in by_group.items():
            if g != best_gid:
                drop_ids.update(i.id for i in grp)

    if not drop_ids:
        return cart_items
    return [i for i in cart_items if i.id not in drop_ids]
