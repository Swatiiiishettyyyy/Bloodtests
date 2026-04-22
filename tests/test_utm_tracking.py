"""
UTM tracking CRUD and new-user link behavior (SQLite).
Run with: pytest tests/test_utm_tracking.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base  # noqa: E402
from Login_module.User.user_model import User  # noqa: E402
from Utm_tracking_module.Utm_tracking_model import UtmTracking  # noqa: E402
from Utm_tracking_module.Utm_tracking_crud import (  # noqa: E402
    create_utm_tracking_row,
    link_utm_rows_for_new_user,
)


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, UtmTracking.__table__],
    )
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def test_create_and_link_utm_for_new_user():
    Session = _session_factory()
    db = Session()
    try:
        user = User(mobile="9876543210")
        db.add(user)
        db.commit()
        db.refresh(user)

        row = create_utm_tracking_row(
            db,
            fingerprint="fp-test-1",
            landing_url="https://example.com/landing?utm_source=google",
            utm_source="google",
            utm_medium="cpc",
        )
        db.commit()

        n = link_utm_rows_for_new_user(
            db,
            fingerprint="fp-test-1",
            user_id=user.id,
            phone=user.mobile,
        )
        db.commit()

        assert n >= 1
        db.refresh(row)
        assert row.user_id == user.id
        assert row.phone == user.mobile
    finally:
        db.close()


def test_link_skips_rows_already_linked():
    Session = _session_factory()
    db = Session()
    try:
        u1 = User(mobile="9876543211")
        u2 = User(mobile="9876543212")
        db.add_all([u1, u2])
        db.commit()
        db.refresh(u1)
        db.refresh(u2)

        create_utm_tracking_row(
            db,
            fingerprint="fp-shared",
            landing_url="https://example.com/a",
        )
        create_utm_tracking_row(
            db,
            fingerprint="fp-shared",
            landing_url="https://example.com/b",
            user_id=u1.id,
            phone=u1.mobile,
        )
        db.commit()

        n = link_utm_rows_for_new_user(
            db,
            fingerprint="fp-shared",
            user_id=u2.id,
            phone=u2.mobile,
        )
        db.commit()

        assert n == 1
        rows = db.query(UtmTracking).filter(UtmTracking.fingerprint == "fp-shared").all()
        assert len(rows) == 2
        anonymous_linked = [r for r in rows if r.landing_url == "https://example.com/a"]
        assert len(anonymous_linked) == 1
        assert anonymous_linked[0].user_id == u2.id
        already = [r for r in rows if r.landing_url == "https://example.com/b"]
        assert already[0].user_id == u1.id
    finally:
        db.close()


def test_link_empty_fingerprint_noop():
    Session = _session_factory()
    db = Session()
    try:
        assert link_utm_rows_for_new_user(db, "", 1, "x") == 0
        assert link_utm_rows_for_new_user(db, "   ", 1, "x") == 0
    finally:
        db.close()
