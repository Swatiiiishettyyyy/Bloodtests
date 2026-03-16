"""
Unit tests for Thyrocare integration.
Run with: pytest tests/test_thyrocare_integration.py -v
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_extract_payable_amount_from_breakup():
    """Test payable amount extraction from Thyrocare price-breakup response."""
    from Thyrocare_module.thyrocare_cart_service import extract_payable_amount_from_breakup

    assert extract_payable_amount_from_breakup({}) == 0.0
    assert extract_payable_amount_from_breakup(None) == 0.0

    breakup = {"rates": {"netPayableAmount": "1300"}}
    assert extract_payable_amount_from_breakup(breakup) == 1300.0

    breakup = {"rates": {"totalSellingPrice": 1200}}
    assert extract_payable_amount_from_breakup(breakup) == 1200.0

    breakup = {"price": {"netPayableAmount": "999"}}
    assert extract_payable_amount_from_breakup(breakup) == 999.0


def test_thyrocare_status_mapping():
    """Test Thyrocare status to Nucleoseq OrderStatus mapping."""
    from Thyrocare_module.thyrocare_order_status_service import (
        _map_thyrocare_status,
        _is_terminal_state,
    )
    from Orders_module.Order_model import OrderStatus

    assert _map_thyrocare_status("YET TO ASSIGN") == OrderStatus.CONFIRMED
    assert _map_thyrocare_status("ASSIGNED") == OrderStatus.PHLEBO_ASSIGNED
    assert _map_thyrocare_status("ACCEPTED") == OrderStatus.PHLEBO_ASSIGNED
    assert _map_thyrocare_status("STARTED") == OrderStatus.PHLEBO_EN_ROUTE
    assert _map_thyrocare_status("ARRIVED") == OrderStatus.PHLEBO_ARRIVED
    assert _map_thyrocare_status("CONFIRMED") == OrderStatus.SAMPLE_COLLECTED
    assert _map_thyrocare_status("DONE") == OrderStatus.REPORT_READY
    assert _map_thyrocare_status("REPORTED") == OrderStatus.REPORT_READY
    assert _map_thyrocare_status("CANCELLED") == OrderStatus.CANCELLED

    assert _is_terminal_state("DONE") is True
    assert _is_terminal_state("REPORTED") is True
    assert _is_terminal_state("CANCELLED") is True
    assert _is_terminal_state("ASSIGNED") is False


def test_extract_price_from_breakup():
    """Test price object extraction for Thyrocare create-order payload."""
    from Thyrocare_module.thyrocare_orders_service import _extract_price_from_breakup

    assert _extract_price_from_breakup({}) == {}
    assert _extract_price_from_breakup({"rates": {"discounts": [], "incentives": {}}}) == {
        "discounts": [],
        "incentivePasson": {},
    }
    assert _extract_price_from_breakup({"discounts": [1], "incentivePasson": {"x": 1}}) == {
        "discounts": [1],
        "incentivePasson": {"x": 1},
    }


def test_normalize_thyrocare_errors():
    """Test Thyrocare error normalization."""
    from Thyrocare_module.thyrocare_service import normalize_thyrocare_errors

    errors = {"errors": [{"code": "BAD_REQUEST", "message": "Invalid pincode"}]}
    result = normalize_thyrocare_errors(errors)
    assert result["message"] == "Invalid pincode"
    assert "error_code" in result or "raw" in result
