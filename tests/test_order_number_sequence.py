"""Internal order_number sequence (SQLite)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base  # noqa: E402
from Orders_module.order_number_counter_model import (  # noqa: E402
    OrderNumberCounter,
    ORDER_NUMBER_SEED_LAST,
)
from Orders_module.Order_crud import generate_order_number  # noqa: E402


def test_order_number_sequence():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine, tables=[OrderNumberCounter.__table__])
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    db = Session()
    try:
        db.add(OrderNumberCounter(id=1, last_value=ORDER_NUMBER_SEED_LAST))
        db.commit()

        assert generate_order_number(db) == "ORD0000000001"
        db.commit()
        assert generate_order_number(db) == "ORD0000000002"
        db.commit()

        row = db.query(OrderNumberCounter).filter_by(id=1).one()
        assert row.last_value == 2
    finally:
        db.close()

