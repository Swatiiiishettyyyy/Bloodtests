"""
Razorpay payment gateway integration service.
"""
import razorpay
import logging
import hmac
import hashlib
from typing import Dict, Any, Optional
from dotenv import load_dotenv
import os
import requests

load_dotenv()

logger = logging.getLogger(__name__)

# Razorpay configuration
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

_RAZORPAY_ENABLED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)
if not _RAZORPAY_ENABLED:
    # Dev-friendly: allow backend to boot without Razorpay configured.
    # Razorpay-dependent endpoints will fail with a clear 503 instead of crashing on import.
    logger.warning("RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET not set. Razorpay features disabled.")

if not RAZORPAY_WEBHOOK_SECRET:
    logger.warning("RAZORPAY_WEBHOOK_SECRET not set. Webhook signature verification will fail.")

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if _RAZORPAY_ENABLED else None

# Set API version (optional)
# razorpay_client.set_app_details({"title": "Nucleotide", "version": "1.0.0"})


def create_razorpay_order(amount: float, currency: str = "INR", receipt: Optional[str] = None, notes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Create a Razorpay order.
    
    Args:
        amount: Amount in rupees (will be converted to paise)
        currency: Currency code (default: INR)
        receipt: Receipt ID for internal tracking
        notes: Additional notes/metadata
    
    Returns:
        Razorpay order object
    """
    if not _RAZORPAY_ENABLED or razorpay_client is None:
        raise ConnectionError("Razorpay is not configured on this server (missing RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET).")
    try:
        # Convert rupees to paise (multiply by 100)
        amount_in_paise = int(amount * 100)
        
        order_data = {
            "amount": amount_in_paise,
            "currency": currency,
            "payment_capture": 1,  # Auto-capture payment
        }
        
        if receipt:
            order_data["receipt"] = receipt
        
        if notes:
            order_data["notes"] = notes
        
        order = razorpay_client.order.create(data=order_data)
        
        logger.info(f"Razorpay order created: {order.get('id')} for amount {amount}")
        return order
    
    except razorpay.errors.BadRequestError as e:
        logger.error(f"Razorpay bad request error: {e}")
        raise ValueError(f"Invalid request to Razorpay: {str(e)}")
    except razorpay.errors.ServerError as e:
        logger.error(f"Razorpay server error: {e}")
        raise ConnectionError(f"Razorpay server error: {str(e)}")
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, OSError) as e:
        logger.error(f"Network error connecting to Razorpay: {e}", exc_info=True)
        raise ConnectionError(
            f"Unable to reach Razorpay payment gateway. "
            f"This is a network connectivity issue on the server side. "
            f"Details: {type(e).__name__}: {str(e)}"
        )
    except Exception as e:
        # Check if it's a connection-related error wrapped in a generic exception
        err_str = str(e).lower()
        if any(kw in err_str for kw in ("connection", "timeout", "reset", "refused", "network", "aborted")):
            logger.error(f"Network-related error creating Razorpay order: {e}", exc_info=True)
            raise ConnectionError(
                f"Unable to reach Razorpay payment gateway (network error). "
                f"Details: {type(e).__name__}: {str(e)}"
            )
        logger.error(f"Unexpected error creating Razorpay order: {e}", exc_info=True)
        raise ValueError(f"Failed to create Razorpay order: {type(e).__name__}: {str(e)}")


def verify_payment_signature(razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
    """
    Verify Razorpay payment signature to ensure payment authenticity.
    
    Args:
        razorpay_order_id: Razorpay order ID
        razorpay_payment_id: Razorpay payment ID
        razorpay_signature: Signature received from Razorpay
    
    Returns:
        True if signature is valid, False otherwise
    """
    if not _RAZORPAY_ENABLED or not RAZORPAY_KEY_SECRET:
        logger.error("Razorpay is not configured; cannot verify payment signature.")
        return False
    try:
        # Create message to verify
        message = f"{razorpay_order_id}|{razorpay_payment_id}"
        
        # Generate expected signature
        generated_signature = hmac.new(
            RAZORPAY_KEY_SECRET.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Compare signatures (use constant-time comparison to prevent timing attacks)
        is_valid = hmac.compare_digest(generated_signature, razorpay_signature)
        
        if is_valid:
            logger.info(f"Payment signature verified for order: {razorpay_order_id}")
        else:
            logger.warning(f"Invalid payment signature for order: {razorpay_order_id}")
        
        return is_valid
    
    except Exception as e:
        logger.error(f"Error verifying payment signature: {e}")
        return False


def get_payment_details(payment_id: str) -> Optional[Dict[str, Any]]:
    """
    Get payment details from Razorpay.
    
    Args:
        payment_id: Razorpay payment ID
    
    Returns:
        Payment details or None if not found
    """
    if not _RAZORPAY_ENABLED or razorpay_client is None:
        logger.error("Razorpay is not configured; cannot fetch payment details.")
        return None
    try:
        payment = razorpay_client.payment.fetch(payment_id)
        return payment
    except razorpay.errors.BadRequestError as e:
        logger.error(f"Payment not found: {payment_id}, error: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching payment details: {e}")
        return None


def get_order_details(razorpay_order_id: str) -> Optional[Dict[str, Any]]:
    """
    Get Razorpay order details.
    
    Args:
        razorpay_order_id: Razorpay order ID
    
    Returns:
        Order details or None if not found
    """
    if not _RAZORPAY_ENABLED or razorpay_client is None:
        logger.error("Razorpay is not configured; cannot fetch order details.")
        return None
    try:
        order = razorpay_client.order.fetch(razorpay_order_id)
        return order
    except razorpay.errors.BadRequestError as e:
        logger.error(f"Order not found: {razorpay_order_id}, error: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching order details: {e}")
        return None


def create_razorpay_customer(
    name: Optional[str] = None,
    email: Optional[str] = None,
    contact: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a Razorpay customer that can be reused for invoices.

    At least one of name, email, or contact should be provided.
    """
    data: Dict[str, Any] = {}
    if name:
        data["name"] = name
    if email:
        data["email"] = email
    if contact:
        data["contact"] = contact

    if not data:
        raise ValueError("Cannot create Razorpay customer without any of name, email, or contact")

    if not _RAZORPAY_ENABLED or razorpay_client is None:
        raise ConnectionError("Razorpay is not configured on this server (missing RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET).")
    try:
        customer = razorpay_client.customer.create(data=data)
        logger.info(f"Razorpay customer created: {customer.get('id')}")
        return customer
    except razorpay.errors.BadRequestError as e:
        error_str = str(e).lower()
        # Razorpay returns 400 when a customer with the same email/contact already exists.
        # In that case, fetch the existing customer instead of failing.
        if "already exists" in error_str and data.get("email"):
            logger.info(
                f"Razorpay customer already exists for email {data['email']}. "
                f"Fetching existing customer."
            )
            try:
                # Razorpay customer search by email
                result = razorpay_client.customer.all({"email": data["email"]})
                items = result.get("items") or []
                if items:
                    existing = items[0]
                    logger.info(f"Reusing existing Razorpay customer: {existing.get('id')}")
                    return existing
            except Exception as fetch_err:
                logger.error(f"Failed to fetch existing Razorpay customer: {fetch_err}")
        logger.error(f"Razorpay customer bad request error: {e}")
        raise ValueError(f"Invalid request to Razorpay while creating customer: {str(e)}")
    except razorpay.errors.ServerError as e:
        logger.error(f"Razorpay customer server error: {e}")
        raise ValueError(f"Razorpay server error while creating customer: {str(e)}")
    except Exception as e:
        logger.error(f"Error creating Razorpay customer: {e}")
        raise ValueError(f"Failed to create Razorpay customer: {str(e)}")


def create_razorpay_invoice_for_order(
    customer_id: str,
    order_number: str,
    total_amount: float,
    currency: str = "INR",
    email: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a Razorpay invoice for a confirmed order using an existing customer_id.

    This uses the public Razorpay example for creating an invoice with customer_id:
    https://www.postman.com/razorpaydev/razorpay-public-workspace/request/llu74wr/create-an-invoice-with-customer-id?tab=body
    """
    if not _RAZORPAY_ENABLED or razorpay_client is None:
        raise ConnectionError("Razorpay is not configured on this server (missing RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET).")
    try:
        import time as _time
        amount_in_paise = int(total_amount * 100)

        invoice_data: Dict[str, Any] = {
            "type": "invoice",
            "date": int(_time.time()),  # Required by Razorpay: Unix timestamp of invoice date
            "customer_id": customer_id,
            "currency": currency,
            "line_items": [
                {
                    "name": f"Order {order_number}",
                    "amount": amount_in_paise,
                    "currency": currency,
                    "quantity": 1,
                }
            ],
            # We only create the invoice now; email/SMS sending can be enabled later
            "sms_notify": 0,
            "email_notify": 0,
            "notes": {
                "order_number": order_number,
            },
        }

        # Optionally attach email so it can be used later from Razorpay side
        if email:
            invoice_data["email"] = email

        # Log invoice creation attempt with key details
        logger.info(f"Creating Razorpay invoice for order {order_number} (customer_id={customer_id}, amount={total_amount} {currency})")
        logger.debug(f"Invoice payload: {invoice_data}")

        invoice = razorpay_client.invoice.create(data=invoice_data)

        # Validate response contains required fields
        if not invoice:
            logger.error(f"Razorpay invoice.create() returned None for order {order_number}")
            raise ValueError("Razorpay invoice.create() returned None - API call may have failed silently")

        if not invoice.get("id"):
            logger.error(f"Invoice response missing 'id' field for order {order_number}. Response: {invoice}")
            raise ValueError(f"Invoice response incomplete - missing 'id' field. Got: {list(invoice.keys())}")

        logger.info(f"Razorpay invoice created successfully: {invoice.get('id')} for order {order_number} (status: {invoice.get('status')})")
        return invoice
    except razorpay.errors.BadRequestError as e:
        logger.error(f"Razorpay invoice bad request error for order {order_number}: {e}")
        logger.error("Possible causes: Invalid customer_id, invoice feature not enabled in Razorpay account, or API key lacks invoice permissions")
        logger.error("Check Razorpay Dashboard > Settings > Invoices and Settings > API Keys > Permissions")
        raise ValueError(f"Invalid request to Razorpay: {str(e)}")
    except razorpay.errors.ServerError as e:
        logger.error(f"Razorpay invoice server error for order {order_number}: {e}")
        logger.error("Razorpay API is experiencing issues. This may be temporary - retry may succeed.")
        raise ValueError(f"Razorpay server error: {str(e)}")
    except Exception as e:
        logger.error(f"Error creating Razorpay invoice for order {order_number}: {e}", exc_info=True)
        raise ValueError(f"Failed to create Razorpay invoice: {str(e)}")


def verify_webhook_signature(webhook_body: str, webhook_signature: str) -> bool:
    """
    Verify Razorpay webhook signature using webhook secret.
    
    Args:
        webhook_body: Raw webhook request body (as string)
        webhook_signature: X-Razorpay-Signature header value
    
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        if not RAZORPAY_WEBHOOK_SECRET:
            logger.error("RAZORPAY_WEBHOOK_SECRET not configured. Cannot verify webhook signature.")
            return False
        
        # Generate expected signature using webhook secret
        generated_signature = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode('utf-8'),
            webhook_body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Compare signatures (use constant-time comparison to prevent timing attacks)
        is_valid = hmac.compare_digest(generated_signature, webhook_signature)
        
        if is_valid:
            logger.info("Webhook signature verified successfully")
        else:
            logger.warning("Invalid webhook signature")
        
        return is_valid
    
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {e}")
        return False


