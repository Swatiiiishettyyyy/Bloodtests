"""Tests for Thyrocare post-payment booking bucket logic."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_normalize_appt_time():
    from Thyrocare_module.thyrocare_booking_service import _normalize_appt_time

    assert _normalize_appt_time(None) is None
    assert _normalize_appt_time("  09:30  ") == "09:30"
    assert _normalize_appt_time("") is None


def test_visit_bucket_key_full_appt():
    from Thyrocare_module.thyrocare_booking_service import _visit_bucket_key_for_order_item

    db = MagicMock()
    snap = MagicMock()
    snap.product_data = {"appointment_date": "2026-04-20", "appointment_start_time": "09:00"}
    db.query.return_value.filter.return_value.first.return_value = snap

    oi = MagicMock()
    oi.id = 101
    oi.snapshot_id = 55
    oi.address_id = 7

    key = _visit_bucket_key_for_order_item(db, oi)
    assert key == (7, "2026-04-20", "09:00")


def test_visit_bucket_key_missing_appt_is_isolated():
    from Thyrocare_module.thyrocare_booking_service import _visit_bucket_key_for_order_item

    db = MagicMock()
    snap = MagicMock()
    snap.product_data = {"appointment_date": "2026-04-20"}  # no time
    db.query.return_value.filter.return_value.first.return_value = snap

    oi = MagicMock()
    oi.id = 202
    oi.snapshot_id = 56
    oi.address_id = 7

    key = _visit_bucket_key_for_order_item(db, oi)
    assert key == ("__incomplete_appt__", 202)


def test_same_address_date_time_buckets_together():
    """Two order lines with same visit key should share one bucket when grouped."""
    from collections import defaultdict
    from Thyrocare_module.thyrocare_booking_service import _visit_bucket_key_for_order_item

    db = MagicMock()

    def make_snap(d, t):
        s = MagicMock()
        s.product_data = {"appointment_date": d, "appointment_start_time": t}
        return s

    def mock_first(snap):
        m = MagicMock()
        m.filter.return_value.first.return_value = snap
        return m

    oi1 = MagicMock(id=1, snapshot_id=1, address_id=10)
    oi2 = MagicMock(id=2, snapshot_id=2, address_id=10)

    db.query.side_effect = [
        mock_first(make_snap("2026-04-20", "10:00")),
        mock_first(make_snap("2026-04-20", "10:00")),
    ]

    groups = defaultdict(list)
    for oi in (oi1, oi2):
        key = _visit_bucket_key_for_order_item(db, oi)
        groups[key].append(oi)

    assert len(groups) == 1
    assert len(next(iter(groups.values()))) == 2


def test_different_start_time_splits_buckets():
    from collections import defaultdict
    from Thyrocare_module.thyrocare_booking_service import _visit_bucket_key_for_order_item

    db = MagicMock()

    def make_snap(d, t):
        s = MagicMock()
        s.product_data = {"appointment_date": d, "appointment_start_time": t}
        return s

    snaps = [make_snap("2026-04-20", "09:00"), make_snap("2026-04-20", "11:00")]
    q_calls = iter(snaps)

    def query_side_effect(*args, **kwargs):
        m = MagicMock()
        m.filter.return_value.first.return_value = next(q_calls)
        return m

    db.query.side_effect = query_side_effect

    oi1 = MagicMock(id=1, snapshot_id=1, address_id=10)
    oi2 = MagicMock(id=2, snapshot_id=2, address_id=10)

    groups = defaultdict(list)
    for oi in (oi1, oi2):
        key = _visit_bucket_key_for_order_item(db, oi)
        groups[key].append(oi)

    assert len(groups) == 2
