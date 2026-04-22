"""
Internal service to book Thyrocare orders after payment confirmation.
Called from the Razorpay webhook handler.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests as _requests
from sqlalchemy.orm import Session

from config import settings
from Cart_module.Cart_model import CartItem, ProductType
from Orders_module.Order_model import OrderItem, Order
from Address_module.Address_model import Address
from Member_module.Member_model import Member
from Thyrocare_module.Thyrocare_model import ThyrocareProduct
from Thyrocare_module.thyrocare_service import ThyrocareService

logger = logging.getLogger(__name__)

# First milestone Thyrocare uses after a successful partner booking; matches webhook orderStatus
# vocabulary so GET /thyrocare/orders/.../order-details shows "Order Booked" immediately.
_THYROCARE_INITIAL_WEBHOOK_ORDER_STATUS = "YET TO ASSIGN"


def _normalize_appt_time(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _blood_test_appt_from_snapshot(db: Session, oi: OrderItem) -> Tuple[Optional[str], Optional[str]]:
    """Return (appointment_date_str|None, appointment_start_time|None) from order item snapshot."""
    if not oi.snapshot_id:
        return None, None
    from Orders_module.Order_model import OrderSnapshot

    snap = db.query(OrderSnapshot).filter(OrderSnapshot.id == oi.snapshot_id).first()
    if not snap or not snap.product_data:
        return None, None
    pd = snap.product_data
    d = pd.get("appointment_date")
    if d is not None:
        d = str(d).strip() or None
    t = _normalize_appt_time(pd.get("appointment_start_time"))
    return d, t


def _visit_bucket_key_for_order_item(db: Session, oi: OrderItem) -> Tuple[Any, ...]:
    """
    One Thyrocare home visit = one address + one appointment (date + start time).
    All patients on that visit share the same bucket (multiple members, merged items per patient).

    If appointment is missing from the snapshot, bucket alone so unrelated lines are not merged.
    """
    d, t = _blood_test_appt_from_snapshot(db, oi)
    if d is None or t is None:
        return ("__incomplete_appt__", oi.id)
    return (oi.address_id, d, t)


def normalize_mobile(mob: str) -> str:
    mob = mob.strip()
    if mob.startswith("+91-"):
        return mob
    if mob.startswith("+91"):
        return f"+91-{mob[3:]}"
    if mob.startswith("91") and len(mob) == 12:
        return f"+91-{mob[2:]}"
    return f"+91-{mob}"


def book_thyrocare_for_order(db: Session, order: Order) -> None:
    """
    After payment confirmation, find all blood test order_items for this order
    and book them with Thyrocare. Updates thyrocare_booking_status on each item.

    This is called from the webhook handler and must NOT raise exceptions
    (failures are logged and stored on the order_item).
    """
    try:
        # Find blood test order items for this order that haven't been booked yet
        blood_test_items = db.query(OrderItem).filter(
            OrderItem.order_id == order.id,
            OrderItem.thyrocare_product_id != None,
            OrderItem.thyrocare_order_id == None,
        ).all()

        if not blood_test_items:
            return

        from collections import defaultdict

        groups: Dict[Tuple[Any, ...], List[OrderItem]] = defaultdict(list)
        for oi in blood_test_items:
            key = _visit_bucket_key_for_order_item(db, oi)
            groups[key].append(oi)

        service = ThyrocareService()

        def _sort_key(k: Tuple[Any, ...]):
            if k and k[0] == "__incomplete_appt__":
                return (0, str(k[1]))
            return (1, k[0] or 0, str(k[1] or ""), str(k[2] or ""))

        sorted_keys = sorted(groups.keys(), key=_sort_key)

        for idx, key in enumerate(sorted_keys, start=1):
            items = groups[key]
            ref_order_no = f"{order.order_number}_{idx}"
            try:
                _book_group(db, order, items, service, ref_order_no)
            except _requests.HTTPError as e:
                error_body = e.response.text if e.response else str(e)
                logger.error(
                    f"Thyrocare booking failed for order {order.order_number}, bucket {key}: {e} | Response: {error_body}"
                )
                for oi in items:
                    oi.thyrocare_booking_status = "FAILED"
                    oi.thyrocare_booking_error = f"{str(e)[:200]} | {error_body[:300]}"
                db.commit()
            except Exception as e:
                logger.error(f"Thyrocare booking failed for order {order.order_number}, bucket {key}: {e}")
                for oi in items:
                    oi.thyrocare_booking_status = "FAILED"
                    oi.thyrocare_booking_error = str(e)[:500]
                db.commit()

    except Exception as e:
        logger.error(f"Thyrocare booking service error for order {order.order_number}: {e}")


def _book_group(db: Session, order: Order, items: List[OrderItem], service: ThyrocareService, ref_order_no: str):
    """Book one home-collection visit: one address, one appointment, patients[] with merged items per member."""
    from Login_module.User.user_model import User

    if not items:
        raise ValueError("No order items to book")

    address_id = items[0].address_id
    if address_id is None:
        raise ValueError("address_id missing on order item")

    appointment_date: Optional[str] = None
    appointment_start_time: Optional[str] = None

    for oi in items:
        if oi.address_id != address_id:
            raise ValueError(f"Inconsistent address_id in booking group: {oi.address_id} vs {address_id}")
        d, t = _blood_test_appt_from_snapshot(db, oi)
        if d and t:
            if appointment_date is None:
                appointment_date = d
                appointment_start_time = t
            elif appointment_date != d or _normalize_appt_time(appointment_start_time) != t:
                raise ValueError("Inconsistent appointment date/time across items in the same booking group")

    address = db.query(Address).filter(Address.id == address_id).first()
    if not address:
        raise ValueError(f"Address {address_id} not found")

    user = db.query(User).filter(User.id == order.user_id).first()

    if not appointment_date or not appointment_start_time:
        product_ids = {oi.thyrocare_product_id for oi in items if oi.thyrocare_product_id}
        cart_item = None
        for pid in sorted(product_ids):
            cand = (
                db.query(CartItem)
                .filter(
                    CartItem.user_id == order.user_id,
                    CartItem.thyrocare_product_id == pid,
                    CartItem.product_type == ProductType.BLOOD_TEST,
                    CartItem.address_id == address_id,
                    CartItem.appointment_date != None,
                )
                .order_by(CartItem.created_at.desc())
                .first()
            )
            if cand:
                cart_item = cand
                break
        if cart_item:
            appointment_date = str(cart_item.appointment_date)
            appointment_start_time = cart_item.appointment_start_time

    if not appointment_date or not appointment_start_time:
        raise ValueError("Appointment date/time not set for this blood test")

    gender_map = {"M": "MALE", "F": "FEMALE", "MALE": "MALE", "FEMALE": "FEMALE"}

    from Login_module.Utils.phone_encryption import decrypt_phone

    def _safe_decrypt(val):
        if not val:
            return ""
        try:
            return decrypt_phone(val)
        except Exception as _e:
            raise ValueError(f"Failed to decrypt phone number for booking: {_e}") from _e

    user_mobile_decrypted = _safe_decrypt(user.mobile if user else "")

    by_member: Dict[int, List[OrderItem]] = {}
    for oi in items:
        if oi.member_id is None:
            raise ValueError("member_id is required for Thyrocare booking")
        by_member.setdefault(oi.member_id, []).append(oi)

    patients: List[Dict[str, Any]] = []
    incentive_total = 0

    for member_id in sorted(by_member.keys()):
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise ValueError(f"Member {member_id} not found")

        gender = gender_map.get(str(member.gender).upper(), "MALE")
        mobile = _safe_decrypt(member.mobile) or user_mobile_decrypted or ""
        email = member.email or (user.email if user else "") or "noreply@nucleotide.life"

        line_items = []
        for oi in sorted(by_member[member_id], key=lambda x: (x.thyrocare_product_id or 0, x.id)):
            if not oi.thyrocare_product_id:
                raise ValueError(f"OrderItem {oi.id} missing thyrocare_product_id")
            product = db.query(ThyrocareProduct).filter(ThyrocareProduct.id == oi.thyrocare_product_id).first()
            if not product:
                raise ValueError(f"ThyrocareProduct {oi.thyrocare_product_id} not found")
            incentive_total += int(product.notational_incentive or 0)
            line_items.append({
                "id": product.thyrocare_id,
                "type": product.type,
                "name": product.name,
                "origin": {
                    "enteredBy": user.name if user else str(order.user_id),
                    "platform": "web"
                }
            })

        patients.append({
            "name": member.name,
            "gender": gender,
            "age": member.age,
            "ageType": "YEAR",
            "contactNumber": normalize_mobile(mobile) if mobile else "+91-0000000000",
            "email": email,
            "attributes": {
                "ulcUniqueCode": "",
                "patientAddress": f"{address.street_address}, {address.city}",
                "externalPatientId": str(member.id)
            },
            "items": line_items,
            "documents": []
        })

    user_mobile = normalize_mobile(user_mobile_decrypted) if user_mobile_decrypted else "+91-0000000000"
    unique_product_ids = {oi.thyrocare_product_id for oi in items if oi.thyrocare_product_id}
    tracking_product_id = sorted(unique_product_ids)[0] if len(unique_product_ids) == 1 else None

    order_payload = {
        "address": {
            "houseNo": address.address_label or "",
            "street": address.street_address or "",
            "addressLine1": " ".join(filter(None, [
                address.address_label,
                address.street_address,
                address.landmark,
                address.city
            ])),
            "addressLine2": address.landmark or "",
            "landmark": address.landmark or "",
            "city": address.city,
            "state": address.state,
            "country": address.country,
            "pincode": int("".join(filter(str.isdigit, address.postal_code or "")) or "0")
        },
        "email": user.email if user else "noreply@nucleotide.life",
        "contactNumber": user_mobile,
        "appointment": {
            "date": str(appointment_date),
            "startTime": appointment_start_time,
            "timeZone": "IST"
        },
        "origin": {
            "platform": "web",
            "appId": "nucleotide-app",
            "portalType": "b2c",
            "enteredBy": user.name if user else str(order.user_id),
            "source": "Nucleotide"
        },
        "referredBy": {"doctorId": "", "doctorName": ""},
        "paymentDetails": {"payType": "POSTPAID"},
        "attributes": {
            "remarks": "",
            "campId": None,
            "isReportHardCopyRequired": False,
            "refOrderNo": ref_order_no,
            "collectionType": "HOME_COLLECTION",
            "alertMessage": [""]
        },
        "config": {
            "communication": {
                "shareReport": True,
                "shareReceipt": True,
                "shareModes": {"whatsapp": True, "email": True}
            }
        },
        "patients": patients,
        "price": {
            "discounts": [{"type": "COUPON", "code": "0"}],
            "incentivePasson": {
                "type": "FLAT",
                "value": incentive_total
            }
        },
        "orderOptions": {"isPdpcOrder": True}
    }

    logger.info(
        f"Booking Thyrocare order for order {order.order_number}, ref {ref_order_no}, "
        f"products={sorted(unique_product_ids)} members={sorted(by_member.keys())}"
    )
    print(f"[THYROCARE BOOKING] Order: {order.order_number} | Sending payload:\n{__import__('json').dumps(order_payload, indent=2, default=str)}")

    response = _requests.post(
        f"{settings.THYROCARE_BASE_URL}/partners/v1/orders",
        json=order_payload,
        headers=service._auth_headers(),
    )
    print(f"[THYROCARE BOOKING] Order: {order.order_number} | Response status: {response.status_code}")
    print(f"[THYROCARE BOOKING] Order: {order.order_number} | Response body: {response.text}")
    response.raise_for_status()
    result = response.json()

    thyrocare_order_no = result.get("orderNo") or result.get("orderId")
    if not thyrocare_order_no:
        raise ValueError(f"Thyrocare did not return orderNo. Response: {result}")

    for oi in items:
        oi.thyrocare_order_id = thyrocare_order_no
        oi.thyrocare_booking_status = "BOOKED"
        oi.thyrocare_booking_error = None

    if not order.thyrocare_order_id:
        order.thyrocare_order_id = thyrocare_order_no
    if order.thyrocare_booking_status != "FAILED":
        order.thyrocare_booking_status = "BOOKED"

    try:
        from Thyrocare_module.thyrocare_webhook_model import ThyrocareOrderTracking
        from Login_module.Utils.datetime_utils import now_ist as _now_ist

        member_ids = [oi.member_id for oi in items if oi.member_id]
        order_item_ids = [oi.id for oi in items]
        user_id = order.user_id

        tracking = db.query(ThyrocareOrderTracking).filter(
            ThyrocareOrderTracking.thyrocare_order_id == thyrocare_order_no
        ).first()

        if not tracking:
            tracking = ThyrocareOrderTracking(
                thyrocare_order_id=thyrocare_order_no,
                our_order_id=order.id,
                user_id=user_id,
                member_ids=member_ids,
                order_item_ids=order_item_ids,
                thyrocare_product_id=tracking_product_id,
                ref_order_no=ref_order_no,
                current_order_status=_THYROCARE_INITIAL_WEBHOOK_ORDER_STATUS,
                created_at=_now_ist(),
            )
            if appointment_date:
                try:
                    from datetime import datetime as _dt
                    from datetime import timezone as _tz
                    if isinstance(appointment_date, str):
                        parsed = _dt.fromisoformat(appointment_date)
                    else:
                        parsed = appointment_date
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=_tz.utc)
                    tracking.appointment_date = parsed
                except Exception:
                    pass
            db.add(tracking)
        else:
            tracking.our_order_id = order.id
            tracking.user_id = user_id
            tracking.member_ids = member_ids
            tracking.order_item_ids = order_item_ids
            tracking.thyrocare_product_id = tracking_product_id
            tracking.ref_order_no = ref_order_no
            if not tracking.current_order_status:
                tracking.current_order_status = _THYROCARE_INITIAL_WEBHOOK_ORDER_STATUS
            if appointment_date and not tracking.appointment_date:
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    if isinstance(appointment_date, str):
                        parsed = _dt.fromisoformat(appointment_date)
                    else:
                        parsed = appointment_date
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=_tz.utc)
                    tracking.appointment_date = parsed
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Could not upsert thyrocare_order_tracking for {thyrocare_order_no}: {e}")

    db.commit()
    logger.info(f"Thyrocare order {thyrocare_order_no} booked for order {order.order_number}")
