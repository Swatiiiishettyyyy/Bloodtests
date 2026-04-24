from sqlalchemy.orm import Session

from .order_number_sequence_model import OrderNumberSequence

ORDER_NUMBER_BASE = 2627001000


def generate_order_number(db: Session) -> str:
    """
    Generate a unique, sequential numeric order number.

    Concurrency safety: relies on an autoincrement insert (no SELECT MAX(id)+1).
    Order number formula: str(ORDER_NUMBER_BASE + sequence_id)
    """
    row = OrderNumberSequence()
    db.add(row)
    db.flush()  # obtain row.id
    return str(ORDER_NUMBER_BASE + int(row.id))

