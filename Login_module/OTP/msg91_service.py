import logging
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)


class Msg91SendError(RuntimeError):
    pass


def _msisdn(country_code: str, mobile: str) -> str:
    # MSG91 expects numeric MSISDN like "91XXXXXXXXXX"
    cc = (country_code or "").strip().lstrip("+")
    return f"{cc}{mobile}".strip()


def send_flow(country_code: str, mobile: str, template_id: str, variables: Optional[dict] = None) -> Optional[str]:
    """
    Send an MSG91 Flow template with optional variables.
    variables are merged into the recipient object (e.g., {"OTP": "1234"}).
    """
    if not settings.MSG91_AUTH_KEY:
        raise Msg91SendError("MSG91 is not configured (missing MSG91_AUTH_KEY).")
    if not template_id:
        raise Msg91SendError("MSG91 flow template_id is missing.")

    url = settings.MSG91_FLOW_URL
    headers = {
        "accept": "application/json",
        "authkey": settings.MSG91_AUTH_KEY,
        "content-type": "application/json",
    }

    recipient = {"mobiles": _msisdn(country_code, mobile)}
    if variables:
        recipient.update(variables)

    payload = {
        "template_id": template_id,
        "short_url": str(settings.MSG91_SHORT_URL),
        "realTimeResponse": str(settings.MSG91_REALTIME_RESPONSE),
        "recipients": [recipient],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
    except Exception as e:
        raise Msg91SendError(f"MSG91 request failed: {e}") from e

    if resp.status_code < 200 or resp.status_code >= 300:
        body_preview = (resp.text or "").strip()
        body_preview = body_preview[:500]
        raise Msg91SendError(f"MSG91 returned {resp.status_code}: {body_preview}")

    try:
        data = resp.json()
    except Exception:
        data = None

    msg = None
    if isinstance(data, dict):
        msg = data.get("message") or data.get("Message") or data.get("request_id")

    logger.info("MSG91 Flow sent successfully to %s (template_id=%s)", _msisdn(country_code, mobile), template_id)
    return msg


def send_otp_via_msg91_flow(country_code: str, mobile: str, otp: str) -> Optional[str]:
    """
    Send OTP using MSG91 Flow API.

    Returns message/id from MSG91 on success (when available).
    Raises Msg91SendError on failure.
    """
    if not settings.MSG91_OTP_TEMPLATE_ID:
        raise Msg91SendError("MSG91 is not configured (missing MSG91_OTP_TEMPLATE_ID).")
    return send_flow(country_code, mobile, settings.MSG91_OTP_TEMPLATE_ID, variables={"OTP": str(otp)})

