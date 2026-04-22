"""
Twilio Verify API integration - separate from custom OTP (send-otp/verify-otp).
Uses Twilio Verify v2 for SMS verification.
Twilio verify returns access_token and refresh_token same as verify-otp (web cookies or mobile JSON).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from typing import Optional
import re
import logging
import uuid

from config import settings
from deps import get_db
from Login_module.Utils.phone_validation import validate_indian_mobile
from Login_module.Utils import security
from Login_module.Utils.rate_limiter import get_client_ip
from Login_module.User.user_session_crud import get_user_by_mobile, create_user
from Login_module.Device.Device_session_crud import create_device_session
from Login_module.OTP.OTP_schema import VerifiedData, VerifyOTPResponse
from Login_module.Token.Refresh_token_crud import create_refresh_token
from Login_module.Utils.datetime_utils import now_ist
from datetime import timedelta
from Notification_module.Notification_crud import upsert_device_token
from Login_module.Token.Token_audit_crud import log_token_event
from Login_module.Utils.csrf import generate_csrf_token_with_secret
from Utm_tracking_module.Utm_tracking_crud import link_utm_rows_for_new_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/twilio", tags=["auth", "twilio"])

OTP_EXPIRY_SECONDS = settings.OTP_EXPIRY_SECONDS
ACCESS_TOKEN_EXPIRE_SECONDS = settings.ACCESS_TOKEN_EXPIRE_SECONDS
MAX_ACTIVE_SESSIONS = settings.MAX_ACTIVE_SESSIONS


# Request/Response schemas
class TwilioSendVerificationRequest(BaseModel):
    country_code: str = Field(..., example="+91", min_length=1, max_length=5)
    mobile: str = Field(..., example="9876543210", min_length=10, max_length=15)
    channel: str = Field(default="sms", example="sms", description="sms or call")

    @validator("country_code")
    def validate_country_code(cls, v):
        if not re.match(r"^\+?\d{1,4}$", v):
            raise ValueError("Invalid country code format")
        return v if v.startswith("+") else f"+{v}"

    @validator("mobile")
    def validate_mobile(cls, v):
        return validate_indian_mobile(v)

    @validator("channel")
    def validate_channel(cls, v):
        if v not in ("sms", "call"):
            raise ValueError("Channel must be 'sms' or 'call'")
        return v


class TwilioSendVerificationResponse(BaseModel):
    status: str = "success"
    message: str
    verification_sid: str


class TwilioVerifyRequest(BaseModel):
    country_code: str = Field(..., example="+91", min_length=1, max_length=5)
    mobile: str = Field(..., example="9876543210", min_length=10, max_length=15)
    code: str = Field(..., example="123456", min_length=4, max_length=8)
    device_id: str = Field(..., example="device-uuid-or-imei", min_length=1, max_length=255)
    device_platform: str = Field(..., example="web", max_length=50)  # web/mobile/ios/android
    device_details: str = Field(..., example='{"browser":"Chrome", "version":"..."}', max_length=1000)
    fcm_token: Optional[str] = Field(None, max_length=255, description="FCM device token for push notifications")
    utm_fingerprint: Optional[str] = Field(
        None,
        max_length=255,
        description="Same fingerprint as POST /api/utm-tracking to link anonymous rows when is_new_user",
    )

    @validator("country_code")
    def validate_country_code(cls, v):
        if not re.match(r"^\+?\d{1,4}$", v):
            raise ValueError("Invalid country code format")
        return v if v.startswith("+") else f"+{v}"

    @validator("mobile")
    def validate_mobile(cls, v):
        return validate_indian_mobile(v)

    @validator("code")
    def validate_code(cls, v):
        if not re.match(r"^\d{4,8}$", v):
            raise ValueError("Code must be 4-8 digits")
        return v

    @validator("device_id")
    def validate_device_id(cls, v):
        if len(v.strip()) == 0:
            raise ValueError("Device ID cannot be empty")
        return v.strip()

    @validator("utm_fingerprint")
    def validate_utm_fingerprint(cls, v):
        if v is None:
            return None
        s = v.strip()
        return s if s else None


def _get_twilio_client():
    """Get Twilio client; raises if config is missing."""
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio is not configured. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
        )
    if not settings.TWILIO_VERIFY_SERVICE_SID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio Verify service is not configured. Set TWILIO_VERIFY_SERVICE_SID.",
        )
    try:
        from twilio.rest import Client
        return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio package not installed. Run: pip install twilio",
        )


@router.post("/send-verification", response_model=TwilioSendVerificationResponse)
def twilio_send_verification(req: TwilioSendVerificationRequest):
    """
    Send verification code via Twilio Verify API (SMS or call).
    Separate from /auth/send-otp which uses custom OTP flow.
    """
    client = _get_twilio_client()
    phone_number = f"{req.country_code}{req.mobile}"

    try:
        verification = (
            client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID)
            .verifications.create(to=phone_number, channel=req.channel)
        )
        logger.info(f"Twilio verification sent to {phone_number}, sid={verification.sid}")
        return TwilioSendVerificationResponse(
            status="success",
            message="Verification code sent successfully.",
            verification_sid=verification.sid,
        )
    except Exception as e:
        logger.error(f"Twilio send verification failed for {phone_number}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send verification: {str(e)}",
        )


@router.post("/verify", response_model=VerifyOTPResponse)
def twilio_verify(req: TwilioVerifyRequest, request: Request, db: Session = Depends(get_db)):
    """
    Verify code via Twilio Verify API and login user.
    Uses identical login logic as /auth/verify-otp.
    """
    client = _get_twilio_client()
    phone_number = f"{req.country_code}{req.mobile}"
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())

    # 1. Verify Code with Twilio
    try:
        verification_check = (
            client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID)
            .verification_checks.create(to=phone_number, code=req.code)
        )
        if verification_check.status != "approved":
            logger.warning(f"Twilio verification failed for {phone_number}: status={verification_check.status}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code.",
            )
        logger.info(f"Twilio verification approved for {phone_number}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Twilio verify failed for {phone_number}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Verification check failed: {str(e)}",
        )

    # 2. Login Flow (Identical to OTP_router.py)
    try:
        # Get or create user
        try:
            user = get_user_by_mobile(db, req.mobile)
            is_new_user = False
            if not user:
                # No user found - try to create new one
                user = create_user(db, mobile=req.mobile)
                if user:
                    is_new_user = True
                    logger.info(f"New user created (Twilio) | User ID: {user.id} | Phone: {phone_number}")
                else:
                    # Retry logic (same as OTP_router)
                    db.expire_all()
                    user = get_user_by_mobile(db, req.mobile)
                    if user:
                        is_new_user = False
                    else:
                        raise HTTPException(status_code=500, detail="Unable to create or find user.")
        except Exception as e:
            logger.error(f"Error getting/creating user (Twilio): {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Unable to create or find user. Please try again.")

        if not user:
            raise HTTPException(status_code=500, detail="Unable to retrieve user account.")

        # Device & session
        try:
            session = create_device_session(
                db=db,
                user_id=user.id,
                device_id=req.device_id,
                device_platform=req.device_platform or "unknown",
                device_details=req.device_details,
                ip=client_ip,
                user_agent=user_agent,
                expires_in_seconds=ACCESS_TOKEN_EXPIRE_SECONDS,
                max_active_sessions=MAX_ACTIVE_SESSIONS,
                correlation_id=correlation_id
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Session creation failed (Twilio): {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Session creation failed.")

        # Get default member
        from Member_module.Member_model import Member
        default_member = None
        try:
            default_member = db.query(Member).filter(
                Member.user_id == user.id,
                Member.is_self_profile == True,
                Member.is_deleted == False
            ).first()
            if not default_member:
                default_member = db.query(Member).filter(
                    Member.user_id == user.id,
                    Member.is_deleted == False
                ).order_by(Member.created_at.asc()).first()
        except Exception as e:
            logger.warning(f"Error fetching default member: {e}")

        # Platform check
        device_platform = session.device_platform or "unknown"
        is_web = device_platform.lower() in ["web", "desktop_web"]
        token_family_id = str(uuid.uuid4())

        # Build access token
        access_token_data = {
            "sub": str(user.id),
            "session_id": str(session.id),
            "device_platform": device_platform
        }
        if default_member:
            access_token_data["selected_member_id"] = str(default_member.id)

        try:
            access_token = security.create_access_token(
                access_token_data,
                expires_delta=settings.ACCESS_TOKEN_EXPIRE_SECONDS
            )
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail="Token generation failed.")

        # Build refresh token
        refresh_expiry_days = settings.REFRESH_TOKEN_EXPIRE_DAYS_WEB if is_web else settings.REFRESH_TOKEN_EXPIRE_DAYS_MOBILE
        refresh_token_data = {
            "sub": str(user.id),
            "session_id": str(session.id),
            "token_family_id": token_family_id,
            "device_platform": device_platform
        }

        try:
            refresh_token = security.create_refresh_token(
                refresh_token_data,
                expires_delta_days=refresh_expiry_days
            )
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail="Token generation failed.")

        # Store refresh token
        refresh_expires_at = now_ist() + timedelta(days=refresh_expiry_days)
        try:
            create_refresh_token(
                db=db,
                user_id=user.id,
                session_id=session.id,
                token_family_id=token_family_id,
                refresh_token=refresh_token,
                expires_at=refresh_expires_at,
                ip_address=client_ip,
                user_agent=user_agent
            )
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail="Token storage failed.")

        # Update session
        session.refresh_token_family_id = token_family_id
        if is_new_user and req.utm_fingerprint:
            try:
                link_utm_rows_for_new_user(
                    db,
                    fingerprint=req.utm_fingerprint,
                    user_id=user.id,
                    phone=user.mobile,
                )
            except Exception as utm_err:
                logger.warning(
                    "UTM link after new user signup failed (user_id=%s): %s",
                    user.id,
                    utm_err,
                )
        db.commit()
        db.refresh(session)

        # FCM Token
        if req.fcm_token and req.fcm_token.strip():
            try:
                upsert_device_token(db, user.id, req.fcm_token.strip())
            except Exception as fcm_err:
                logger.warning(f"FCM token save failed (Twilio): {fcm_err}")

        # Audit Log
        log_token_event(
            db=db,
            event_type="TOKEN_CREATED",
            user_id=user.id,
            session_id=session.id,
            token_family_id=token_family_id,
            reason=f"Twilio login. Platform: {device_platform}",
            ip_address=client_ip,
            user_agent=user_agent,
            correlation_id=correlation_id
        )

        logger.info(f"Twilio login successful for user {user.id}")

        # Response
        if is_web:
            csrf_token = generate_csrf_token_with_secret(user.id, session.id)
            mobile = user.mobile if user.mobile else ""
            
            response_data = VerifiedData(
                user_id=user.id,
                name=user.name,
                mobile=mobile,
                email=user.email,
                csrf_token=csrf_token,
                is_new_user=is_new_user
            )
            
            response_dict = {
                "status": "success",
                "message": "OTP verified successfully.",
                "data": response_data.dict()
            }
            
            response = JSONResponse(content=response_dict)
            
            # Cookies
            response.set_cookie(
                key="access_token",
                value=access_token,
                path="/",
                httponly=True,
                secure=settings.COOKIE_SECURE,
                samesite=settings.COOKIE_SAMESITE,
                domain=settings.COOKIE_DOMAIN or None,
                max_age=settings.ACCESS_TOKEN_EXPIRE_SECONDS
            )
            
            refresh_token_max_age = int(settings.REFRESH_TOKEN_EXPIRE_DAYS_WEB * 24 * 60 * 60)
            response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                path="/auth/refresh",
                httponly=True,
                secure=settings.COOKIE_SECURE,
                samesite=settings.REFRESH_COOKIE_SAMESITE,
                domain=settings.COOKIE_DOMAIN or None,
                max_age=refresh_token_max_age
            )
            return response
        else:
            mobile = user.mobile if user.mobile else ""
            data = VerifiedData(
                user_id=user.id,
                name=user.name,
                mobile=mobile,
                email=user.email,
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="Bearer",
                expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
                is_new_user=is_new_user
            )
            return VerifyOTPResponse(status="success", message="OTP verified successfully.", data=data)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error in Twilio verify: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Login failed. Please try again.")

