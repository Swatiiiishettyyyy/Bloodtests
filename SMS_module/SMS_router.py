import logging
from fastapi import APIRouter, HTTPException, status
from config import settings
from Login_module.OTP.msg91_service import send_flow, Msg91SendError
from .SMS_schema import (
    SMSSendResponse,
    WelcomeSMSRequest,
    ReportReadySMSRequest,
    OrderConfirmationSMSRequest,
    PhleboBloodSMSRequest,
    PhleboGeneticSMSRequest,
    PostOrderSurveySMSRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sms", tags=["sms"])

_503 = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="SMS service temporarily unavailable.",
)


@router.post("/send-welcome", response_model=SMSSendResponse)
def send_welcome(req: WelcomeSMSRequest):
    try:
        send_flow(req.country_code, req.mobile, settings.MSG91_TEMPLATE_ID_WELCOME_FIRST_TIME, {"name": req.name})
    except Msg91SendError as e:
        logger.error("MSG91 send-welcome failed: %s", e)
        raise _503
    return SMSSendResponse(message="SMS sent successfully.")


@router.post("/send-report-ready", response_model=SMSSendResponse)
def send_report_ready(req: ReportReadySMSRequest):
    try:
        send_flow(req.country_code, req.mobile, settings.MSG91_TEMPLATE_ID_REPORT_READY, {"url": req.url})
    except Msg91SendError as e:
        logger.error("MSG91 send-report-ready failed: %s", e)
        raise _503
    return SMSSendResponse(message="SMS sent successfully.")


@router.post("/send-order-confirmation", response_model=SMSSendResponse)
def send_order_confirmation(req: OrderConfirmationSMSRequest):
    try:
        send_flow(req.country_code, req.mobile, settings.MSG91_TEMPLATE_ID_ORDER_CONFIRMATION, {"order_id": req.order_id})
    except Msg91SendError as e:
        logger.error("MSG91 send-order-confirmation failed: %s", e)
        raise _503
    return SMSSendResponse(message="SMS sent successfully.")


@router.post("/send-phlebo-blood", response_model=SMSSendResponse)
def send_phlebo_blood(req: PhleboBloodSMSRequest):
    try:
        send_flow(req.country_code, req.mobile, settings.MSG91_TEMPLATE_ID_PHLEBO_COLLECTION_BLOOD, {"order_id": req.order_id})
    except Msg91SendError as e:
        logger.error("MSG91 send-phlebo-blood failed: %s", e)
        raise _503
    return SMSSendResponse(message="SMS sent successfully.")


@router.post("/send-phlebo-genetic", response_model=SMSSendResponse)
def send_phlebo_genetic(req: PhleboGeneticSMSRequest):
    try:
        send_flow(req.country_code, req.mobile, settings.MSG91_TEMPLATE_ID_PHLEBO_COLLECTION_GENETIC, {"order_id": req.order_id})
    except Msg91SendError as e:
        logger.error("MSG91 send-phlebo-genetic failed: %s", e)
        raise _503
    return SMSSendResponse(message="SMS sent successfully.")


@router.post("/send-post-order-survey", response_model=SMSSendResponse)
def send_post_order_survey(req: PostOrderSurveySMSRequest):
    try:
        send_flow(req.country_code, req.mobile, settings.MSG91_TEMPLATE_ID_POST_ORDER_SURVEY, {"survey_url": req.survey_url})
    except Msg91SendError as e:
        logger.error("MSG91 send-post-order-survey failed: %s", e)
        raise _503
    return SMSSendResponse(message="SMS sent successfully.")
