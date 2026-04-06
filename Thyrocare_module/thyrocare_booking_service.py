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

        # Group by thyrocare_product_id + address_id (each group = one Thyrocare booking)
        from collections import defaultdict
        groups = defaultdict(list)
        for oi in blood_test_items:
            key = (oi.thyrocare_product_id, oi.address_id)
            groups[key].append(oi)

        service = ThyrocareService()

        for (thyrocare_product_id, address_id), items in groups.items():
            try:
                _book_group(db, order, items, thyrocare_product_id, address_id, service)
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
    cart_item = db.query(CartItem).filter(
        CartItem.user_id == order.user_id,
        CartItem.thyrocare_product_id == thyrocare_product_id,
        CartItem.product_type == ProductType.BLOOD_TEST,
        CartItem.address_id == address_id,
        CartItem.appointment_date != None,
    ).first()

    if not cart_item or not cart_item.appointment_date or not cart_item.appointment_start_time:
        raise ValueError("Appointment date/time not set for this blood test")

    gender_map = {"M": "MALE", "F": "FEMALE", "MALE": "MALE", "FEMALE": "FEMALE"}

    # Build patients
    patients = []
    for oi in items:
        member = db.query(Member).filter(Member.id == oi.member_id).first()
        if not member:
            raise ValueError(f"Member {oi.member_id} not found")

        gender = gender_map.get(str(member.gender).upper(), "MALE")
        mobile = member.mobile or (user.mobile if user else "") or ""
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

    user_mobile = normalize_mobile(user.mobile) if user and user.mobile else "+91-0000000000"
    incentive_value = max(0, int(product.listing_price) - int(product.selling_price))
    ref_order_no = order.order_number[:25] if order.order_number else f"NUC{order.user_id}{uuid.uuid4().hex[:10].upper()}"

    order_payload = {
        "address": {
            "houseNo": address.address_label or "",
            "street": address.street_address,
            "addressLine1": address.locality,
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
            "date": str(cart_item.appointment_date),
            "startTime": cart_item.appointment_start_time,
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
            "discounts": [{"type": "COUPON", "amount": "0"}],
            "incentivePasson": {
                "type": "FLAT",
                "value": incentive_value
            }
        },
        "orderOptions": {"isPdpcOrder": False}
    }

    logger.info(f"Booking Thyrocare order for order {order.order_number}, product {product.thyrocare_id}")

    response = _requests.post(
        f"{settings.THYROCARE_BASE_URL}/partners/v1/orders",
        json=order_payload,
        headers=service._auth_headers(),
    )
    response.raise_for_status()
    result = response.json()

    thyrocare_order_no = result.get("orderNo")
    if not thyrocare_order_no:
        raise ValueError(f"Thyrocare did not return orderNo. Response: {result}")

    # Update all items in this group
    for oi in items:
        oi.thyrocare_order_id = thyrocare_order_no
        oi.thyrocare_booking_status = "BOOKED"
        oi.thyrocare_booking_error = None

    db.commit()
    logger.info(f"Thyrocare order {thyrocare_order_no} booked for order {order.order_number}")
