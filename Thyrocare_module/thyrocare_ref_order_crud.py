"""
Allocate sequential Thyrocare refOrderNo values (thread-safe within DB transaction via row lock).
"""
import logging
from sqlalchemy.orm import Session

from .thyrocare_ref_order_counter_model import (
    ThyrocareRefOrderCounter,
    THYROCARE_REF_ORDER_SEED_LAST,
)

logger = logging.getLogger(__name__)

COUNTER_ROW_ID = 1


def allocate_next_thyrocare_ref_order_no(db: Session) -> str:
    """
    Return next ref order number as a decimal string: 2627001001, 2627001002, ...
    (global sequence; 100th assignment is 2627001100, 101st is 2627001101, etc.)

    Requires a seeded row from migration; if missing (e.g. old DB), creates it.
    """
    row = (
        db.query(ThyrocareRefOrderCounter)
        .filter(ThyrocareRefOrderCounter.id == COUNTER_ROW_ID)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        row = ThyrocareRefOrderCounter(
            id=COUNTER_ROW_ID, last_value=THYROCARE_REF_ORDER_SEED_LAST
        )
        db.add(row)
        db.flush()

    row.last_value = int(row.last_value) + 1
    db.flush()
    ref = str(row.last_value)
    logger.debug("Allocated Thyrocare refOrderNo %s", ref)
    return ref
