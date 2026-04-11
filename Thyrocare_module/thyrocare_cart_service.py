"""
Thyrocare cart service — helpers for cart-related operations.
"""
import logging
import requests as _requests
from typing import List, Optional
from sqlalchemy.orm import Session

from config import settings
from .thyrocare_service import ThyrocareService

logger = logging.getLogger(__name__)


def get_thyrocare_confirmed_amount(db: Session, cart_items: list) -> Optional[float]:
    """
    Internally fetch the confirmed net payable amount from Thyrocare price-breakup API
    for all blood test items in the cart. Returns None if no blood test items found.

    This replaces the need for the frontend to pass thyrocare_confirmed_amount to /orders/create.
    """
    from Cart_module.Cart_model import ProductType
    from Member_module.Member_model import Member
    from .Thyrocare_model import ThyrocareProduct

    gender_map = {"M": "MALE", "F": "FEMALE", "MALE": "MALE", "FEMALE": "FEMALE"}

    # Group blood test items by group_id
    blood_test_groups: dict = {}
    for item in cart_items:
        if getattr(item, "product_type", None) != ProductType.BLOOD_TEST:
            continue
        group_key = item.group_id or f"single_{item.id}"
        blood_test_groups.setdefault(group_key, []).append(item)

    if not blood_test_groups:
        return None

    patients = []
    total_incentive_value = 0

    for group_key, items in blood_test_groups.items():
        product = db.query(ThyrocareProduct).filter(
            ThyrocareProduct.id == items[0].thyrocare_product_id
        ).first()
        if not product:
            raise RuntimeError(f"ThyrocareProduct not found for cart group '{group_key}'")

        member_ids = [i.member_id for i in items]
        members = db.query(Member).filter(Member.id.in_(member_ids)).all()
        members_by_id = {m.id: m for m in members}

        for item in items:
            member = members_by_id.get(item.member_id)
            if not member:
                raise RuntimeError(f"Member {item.member_id} not found for cart group '{group_key}'")
            gender = gender_map.get(str(member.gender).upper(), "MALE")
            patients.append({
                "name": member.name,
                "gender": gender,
                "age": member.age,
                "ageType": "YEAR",
                "items": [
                    {
                        "id": product.thyrocare_id,
                        "type": product.type,
                        "name": product.name,
                        "rate": {
                            "currency": "INR",
                            "mrp": str(int(product.listing_price)) if product.listing_price > 0 else str(int(product.selling_price))
                        }
                    }
                ]
            })

        incentive_value = int(product.notational_incentive)
        total_incentive_value += incentive_value * len(items)

    breakup_payload = {
        "patients": patients,
        "discounts": [{"type": "COUPON", "amount": "0"}],
        "incentivePasson": {
            "type": "FLAT",
            "value": str(total_incentive_value) if total_incentive_value > 0 else "0"
        },
        "isReportHardCopyRequired": False
    }

    logger.info(f"[Internal] Thyrocare price-breakup payload: {breakup_payload}")
    print(f"[THYROCARE PRICE-BREAKUP] Sending payload:\n{__import__('json').dumps(breakup_payload, indent=2)}")

    service = ThyrocareService()
    try:
        response = _requests.post(
            f"{settings.THYROCARE_BASE_URL}/partners/v1/cart/price-breakup",
            json=breakup_payload,
            headers=service._auth_headers(),
        )
        print(f"[THYROCARE PRICE-BREAKUP] Response status: {response.status_code}")
        print(f"[THYROCARE PRICE-BREAKUP] Response body: {response.text}")
        response.raise_for_status()
        data = response.json()
        net_payable = data.get("rates", {}).get("netPayableAmount")
        if net_payable is None:
            raise RuntimeError("Thyrocare price-breakup response did not include netPayableAmount")
        logger.info(f"[Internal] Thyrocare confirmed net payable amount: {net_payable}")
        return float(net_payable)
    except _requests.HTTPError as e:
        error_body = {}
        try:
            error_body = e.response.json()
        except Exception:
            pass
        error_msg = error_body.get("errors", [{}])[0].get("message", str(e))
        logger.error(f"[Internal] Thyrocare price-breakup HTTP error: {error_msg}", exc_info=True)
        raise RuntimeError(f"Could not confirm blood test pricing with Thyrocare: {error_msg}")
    except (ConnectionError, OSError) as e:
        logger.error(f"[Internal] Thyrocare price-breakup network error: {e}", exc_info=True)
        raise RuntimeError(f"Unable to reach Thyrocare to confirm blood test pricing. Please try again.")
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"[Internal] Thyrocare price-breakup failed: {e}", exc_info=True)
        raise RuntimeError(f"Failed to fetch blood test pricing from Thyrocare: {str(e)}")
