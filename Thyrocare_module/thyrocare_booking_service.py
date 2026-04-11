"""
Internal service to book Thyrocare orders after payment confirmation.
Called from the Razorpay webhook handler.
"""
import uuid
import logging
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

        # Group by thyrocare_product_id + address_id + appointment_date (each group = one Thyrocare booking)
        from collections import defaultdict
        groups = defaultdict(list)
        for oi in blood_test_items:
            # Include appointment_date in key to avoid merging items with different appointments
            appt_date = None
            if oi.snapshot_id:
                from Orders_module.Order_model import OrderSnapshot
                snap = db.query(OrderSnapshot).filter(OrderSnapshot.id == oi.snapshot_id).first()
                if snap and snap.product_data:
                    appt_date = snap.product_data.get("appointment_date")
            key = (oi.thyrocare_product_id, oi.address_id, appt_date)
            groups[key].append(oi)

        service = ThyrocareService()

        for (thyrocare_product_id, address_id, _appt_date), items in groups.items():
            try:
                _book_group(db, order, items, thyrocare_product_id, address_id, service)
            except _requests.HTTPError as e:
                error_body = e.response.text if e.response else str(e)
                logger.error(f"Thyrocare booking failed for order {order.order_number}, product {thyrocare_product_id}: {e} | Response: {error_body}")
                for oi in items:
                    oi.thyrocare_booking_status = "FAILED"
                    oi.thyrocare_booking_error = f"{str(e)[:200]} | {error_body[:300]}"
                db.commit()
            except Exception as e:
                logger.error(f"Thyrocare booking failed for order {order.order_number}, product {thyrocare_product_id}: {e}")
                for oi in items:
                    oi.thyrocare_booking_status = "FAILED"
                    oi.thyrocare_booking_error = str(e)[:500]
                db.commit()

    except Exception as e:
        logger.error(f"Thyrocare booking service error for order {order.order_number}: {e}")


def _book_group(db: Session, order: Order, items: list, thyrocare_product_id: int, address_id: int, service: ThyrocareService):
    """Book one group of order items with Thyrocare."""
    from Login_module.User.user_model import User

    product = db.query(ThyrocareProduct).filter(ThyrocareProduct.id == thyrocare_product_id).first()
    if not product:
        raise ValueError(f"ThyrocareProduct {thyrocare_product_id} not found")

    address = db.query(Address).filter(Address.id == address_id).first()
    if not address:
        raise ValueError(f"Address {address_id} not found")

    user = db.query(User).filter(User.id == order.user_id).first()

    # Get appointment from cart items (linked by group_id)
    # Find the cart item for this group to get appointment details
    # Get appointment from order item snapshot (cart items may be soft-deleted by this point)
    from Orders_module.Order_model import OrderSnapshot
    # Find snapshot via order items for this group
    snapshot = None
    for oi in items:
        if oi.snapshot_id:
            snapshot = db.query(OrderSnapshot).filter(
                OrderSnapshot.id == oi.snapshot_id
            ).first()
            if snapshot:
                break

    appointment_date = None
    appointment_start_time = None

    if snapshot and snapshot.product_data:
        appointment_date = snapshot.product_data.get("appointment_date")
        appointment_start_time = snapshot.product_data.get("appointment_start_time")

    # Fallback to cart item if snapshot doesn't have appointment (older orders)
    if not appointment_date or not appointment_start_time:
        cart_item = db.query(CartItem).filter(
            CartItem.user_id == order.user_id,
            CartItem.thyrocare_product_id == thyrocare_product_id,
            CartItem.product_type == ProductType.BLOOD_TEST,
            CartItem.address_id == address_id,
            CartItem.appointment_date != None,
        ).order_by(CartItem.created_at.desc()).first()
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
        except Exception:
            return val  # already plain or unencryptable

    # Decrypt user mobile once
    user_mobile_decrypted = _safe_decrypt(user.mobile if user else "")

    # Build patients
    patients = []
    for oi in items:
        member = db.query(Member).filter(Member.id == oi.member_id).first()
        if not member:
            raise ValueError(f"Member {oi.member_id} not found")

        gender = gender_map.get(str(member.gender).upper(), "MALE")
        mobile = _safe_decrypt(member.mobile) or user_mobile_decrypted or ""
        email = member.email or (user.email if user else "") or "noreply@nucleotide.life"

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
            "items": [{
                "id": product.thyrocare_id,
                "type": product.type,
                "name": product.name,
                "origin": {
                    "enteredBy": user.name if user else str(order.user_id),
                    "platform": "web"
                }
            }],
            "documents": []
        })

    user_mobile = normalize_mobile(user_mobile_decrypted) if user_mobile_decrypted else "+91-0000000000"
    incentive_value = int(product.notational_incentive)
    ref_order_no = order.order_number[:25] if order.order_number else f"NUC{order.user_id}{uuid.uuid4().hex[:10].upper()}"

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
            "pincode": int(address.postal_code.replace(" ", ""))
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
                "value": incentive_value
            }
        },
        "orderOptions": {"isPdpcOrder": True}
    }

    logger.info(f"Booking Thyrocare order for order {order.order_number}, product {product.thyrocare_id}")
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

    # Update all items in this group
    for oi in items:
        oi.thyrocare_order_id = thyrocare_order_no
        oi.thyrocare_booking_status = "BOOKED"
        oi.thyrocare_booking_error = None

    # Also update the order-level thyrocare fields
    order.thyrocare_order_id = thyrocare_order_no
    order.thyrocare_booking_status = "BOOKED"

    # Upsert thyrocare_order_tracking with member_ids + user_id mapping
    try:
        from Thyrocare_module.thyrocare_webhook_model import ThyrocareOrderTracking
        from Login_module.Utils.datetime_utils import now_ist as _now_ist

        member_ids = [oi.member_id for oi in items if oi.member_id]
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
                created_at=_now_ist(),
            )
            # Store appointment date from booking
            if appointment_date:
                try:
                    from datetime import datetime as _dt
                    if isinstance(appointment_date, str):
                        tracking.appointment_date = _dt.fromisoformat(appointment_date)
                    else:
                        tracking.appointment_date = appointment_date
                except Exception:
                    pass
            db.add(tracking)
        else:
            tracking.our_order_id = order.id
            tracking.user_id = user_id
            tracking.member_ids = member_ids
            if appointment_date and not tracking.appointment_date:
                try:
                    from datetime import datetime as _dt
                    if isinstance(appointment_date, str):
                        tracking.appointment_date = _dt.fromisoformat(appointment_date)
                    else:
                        tracking.appointment_date = appointment_date
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Could not upsert thyrocare_order_tracking for {thyrocare_order_no}: {e}")

    db.commit()
    logger.info(f"Thyrocare order {thyrocare_order_no} booked for order {order.order_number}")
