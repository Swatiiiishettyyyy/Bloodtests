import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from config import settings
from Login_module.OTP.msg91_service import send_flow, Msg91SendError
from Login_module.User.user_session_crud import get_user_by_mobile

logger = logging.getLogger(__name__)


class ReportReadyNotificationError(Exception):
    pass


def send_report_ready_notifications(
    db: Session,
    country_code: str,
    mobile: str,
    url: str,
) -> None:
    # SMS — always sent; failure is fatal (caller raises 503)
    try:
        send_flow(country_code, mobile, settings.MSG91_TEMPLATE_ID_REPORT_READY, {"url": url})
    except Msg91SendError as e:
        raise ReportReadyNotificationError(str(e)) from e

    # Email — best-effort; look up user by mobile and send if email is available
    try:
        user = get_user_by_mobile(db, mobile)
        if not user or not user.email:
            return

        import sys as _sys
        _inv_gen = str(Path(__file__).parent.parent / "invoice generation")
        if _inv_gen not in _sys.path:
            _sys.path.append(_inv_gen)
        from report_ready_email import send_report_ready_email

        send_report_ready_email(
            to=user.email,
            name=user.name or "",
            service_account_file=str(Path(__file__).parent.parent / settings.INVOICE_SERVICE_ACCOUNT_PATH),
            sender_email=settings.INFO_SENDER_EMAIL,
        )
        logger.info("Report-ready email sent to %s (mobile %s)", user.email, mobile)
    except Exception as e:
        logger.error("Report-ready email failed for mobile %s: %s", mobile, e, exc_info=True)
