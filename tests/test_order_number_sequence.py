"""Internal order_number sequence (SQLite)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base  # noqa: E402
from Orders_module.order_number_sequence_model import OrderNumberSequence  # noqa: E402
from Orders_module.order_number_service import generate_order_number, ORDER_NUMBER_BASE  # noqa: E402


def test_order_number_sequence():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine, tables=[OrderNumberSequence.__table__])
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    db = Session()
    try:
        assert generate_order_number(db) == str(ORDER_NUMBER_BASE + 1)
        db.commit()
        assert generate_order_number(db) == str(ORDER_NUMBER_BASE + 2)
        db.commit()

        row = db.query(OrderNumberSequence).order_by(OrderNumberSequence.id.desc()).first()
        assert row is not None
        assert row.id == 2
    finally:
        db.close()

