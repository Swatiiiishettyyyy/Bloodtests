"""Thyrocare refOrderNo sequence (SQLite)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base  # noqa: E402
from Thyrocare_module.thyrocare_ref_order_counter_model import (  # noqa: E402
    ThyrocareRefOrderCounter,
    THYROCARE_REF_ORDER_SEED_LAST,
)
from Thyrocare_module.thyrocare_ref_order_crud import (  # noqa: E402
    allocate_next_thyrocare_ref_order_no,
)


def test_thyrocare_ref_order_sequence():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(
        bind=engine,
        tables=[ThyrocareRefOrderCounter.__table__],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    db = Session()
    try:
        db.add(ThyrocareRefOrderCounter(id=1, last_value=THYROCARE_REF_ORDER_SEED_LAST))
        db.commit()

        assert allocate_next_thyrocare_ref_order_no(db) == "2627001001"
        db.commit()
        assert allocate_next_thyrocare_ref_order_no(db) == "2627001002"
        db.commit()

        row = db.query(ThyrocareRefOrderCounter).filter_by(id=1).one()
        assert row.last_value == 2627001002
    finally:
        db.close()


def test_hundredth_ref_is_2627001100():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine, tables=[ThyrocareRefOrderCounter.__table__])
    Session = sessionmaker(bind=engine, future=True)
    db = Session()
    try:
        db.add(ThyrocareRefOrderCounter(id=1, last_value=2627001000))
        db.commit()
        last = None
        for _ in range(100):
            last = allocate_next_thyrocare_ref_order_no(db)
        assert last == "2627001100"
        assert allocate_next_thyrocare_ref_order_no(db) == "2627001101"
        db.commit()
    finally:
        db.close()
