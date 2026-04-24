from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import logging
import uuid
from .OTP_schema import (
    SendOTPRequest,
    SendOTPResponse,
    OTPData,
    VerifyOTPRequest,
    VerifyOTPResponse,
    VerifiedData
)
from deps import get_db
from ..Utils import security
from ..Utils.auth_user import get_current_user
from ..Utils.rate_limiter import get_client_ip
from . import otp_manager
from .msg91_service import send_otp_via_msg91_flow, Msg91SendError
from ..User.user_session_crud import get_user_by_mobile, create_user
from ..User.user_model import User
from ..Device.Device_session_crud import create_device_session, deactivate_session_by_token
from . import OTP_crud
from Notification_module.Notification_crud import upsert_device_token
from Utm_tracking_module.Utm_tracking_crud import link_utm_rows_for_new_user

from config import settings

logger = logging.getLogger(__name__)

# Use settings directly (loaded from .env via Pydantic)
OTP_EXPIRY_SECONDS = settings.OTP_EXPIRY_SECONDS
ACCESS_TOKEN_EXPIRE_SECONDS = settings.ACCESS_TOKEN_EXPIRE_SECONDS
MAX_ACTIVE_SESSIONS = settings.MAX_ACTIVE_SESSIONS

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/send-otp", response_model=SendOTPResponse)
def send_otp(request: SendOTPRequest, http_request: Request, db: Session = Depends(get_db)):
    """
    Send OTP to the provided mobile number.
    """
    phone_number = f"{request.country_code}{request.mobile}"
    correlation_id = str(uuid.uuid4())
    client_ip = get_client_ip(http_request)
    user_agent = http_request.headers.get("user-agent")

    # Block / rate-limit checks (fail closed if Redis is down)
    if otp_manager.is_user_blocked(request.country_code, request.mobile):
        remaining = otp_manager.get_block_remaining_time(request.country_code, request.mobile)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {remaining} seconds."
        )
    if not otp_manager.can_request_otp(request.country_code, request.mobile):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="OTP request limit exceeded. Please try again later."
        )

    # Generate OTP
    otp = otp_manager.generate_otp()
    otp_manager.store_otp(request.country_code, request.mobile, otp, expires_in=OTP_EXPIRY_SECONDS)

    # Create audit log (no OTP values stored)
    OTP_crud.create_otp_audit_log(
        db=db,
        event_type="GENERATED",
        phone_number=phone_number,
        reason="OTP generated and sent",
        ip_address=client_ip,
        user_agent=user_agent,
        correlation_id=correlation_id
    )

    # Send OTP via MSG91 Flow (if configured)
    try:
        send_otp_via_msg91_flow(request.country_code, request.mobile, otp)
        OTP_crud.create_otp_audit_log(
            db=db,
            event_type="SENT",
            phone_number=phone_number,
            reason="OTP sent via MSG91 Flow",
            ip_address=client_ip,
            user_agent=user_agent,
            correlation_id=correlation_id
        )
    except Msg91SendError as e:
        OTP_crud.create_otp_audit_log(
            db=db,
            event_type="FAILED",
            phone_number=phone_number,
            reason=f"OTP send failed (MSG91): {str(e)[:200]}",
            ip_address=client_ip,
            user_agent=user_agent,
            correlation_id=correlation_id
        )
        # Don't leak provider details to client
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to send OTP at the moment. Please try again."
        )

    message = f"OTP sent successfully to {request.mobile}."

    data = OTPData(
        mobile=request.mobile,
        otp=otp if settings.RETURN_OTP_IN_RESPONSE and settings.ENVIRONMENT != "production" else None,
        expires_in=OTP_EXPIRY_SECONDS,
        purpose=request.purpose
    )
    return SendOTPResponse(status="success", message=message, data=data)


@router.post("/verify-otp", response_model=VerifyOTPResponse)
def verify_otp(req: VerifyOTPRequest, request: Request, db: Session = Depends(get_db)):
    """
    Verify OTP and create user session.
    Returns access token on successful verification.
    """
    phone_number = f"{req.country_code}{req.mobile}"
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())

    # Optional bypass (never allow in production unless explicitly enabled)
    if settings.ALLOW_OTP_BYPASS and settings.ENVIRONMENT != "production" and req.otp == settings.OTP_BYPASS_CODE:
        logger.info(f"OTP bypass code used for {phone_number} from IP {client_ip}")
        # Log bypass usage for audit
        try:
            OTP_crud.create_otp_audit_log(
                db=db,
                event_type="VERIFIED",
                phone_number=phone_number,
                device_id=req.device_id,
                reason=f"OTP bypass code used. IP: {client_ip}",
                ip_address=client_ip,
                user_agent=user_agent,
                correlation_id=correlation_id
            )
        except Exception as e:
            logger.warning(f"Failed to create bypass audit log: {e}")
        # Continue with successful verification flow below
    else:
        # Normal OTP verification flow
        # Fetch OTP from Redis (plaintext)
        stored = otp_manager.get_otp(req.country_code, req.mobile)
        if not stored:
            logger.warning(
                f"OTP verification failed - OTP expired or not found | "
                f"Phone: {phone_number} | Device: {req.device_id} | IP: {client_ip}"
            )
            OTP_crud.create_otp_audit_log(
                db=db,
                event_type="FAILED",
                phone_number=phone_number,
                device_id=req.device_id,
                reason="OTP expired or not found",
                ip_address=client_ip,
                user_agent=user_agent,
                correlation_id=correlation_id
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The OTP code has expired. Please request a new one."
            )

        # Compare plaintext (fast check)
        if stored != req.otp:
            logger.warning(
                f"OTP verification failed - Invalid OTP | "
                f"Phone: {phone_number} | Device: {req.device_id} | IP: {client_ip} | "
                f"Attempted OTP: {req.otp[:2]}** (masked)"
            )
            
            OTP_crud.create_otp_audit_log(
                db=db,
                event_type="FAILED",
                phone_number=phone_number,
                device_id=req.device_id,
                reason=f"Invalid OTP. IP: {client_ip}",
                ip_address=client_ip,
                user_agent=user_agent,
                correlation_id=correlation_id
            )
            
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The OTP code you entered is incorrect. Please try again."
            )
        
        # Remove OTP from Redis after successful verification (only for normal flow)
        otp_manager.delete_otp(req.country_code, req.mobile)

    # OTP verified successfully (both normal and bypass)
    try:
        # Get or create user
        # If phone exists and OTP is correct, verification should pass
        try:
            user = get_user_by_mobile(db, req.mobile)
            is_new_user = False
            if not user:
                # No user found - try to create new one
                user = create_user(db, mobile=req.mobile)
                if user:
                    is_new_user = True
                    logger.info(
                        f"New user created during OTP verification | "
                        f"User ID: {user.id} | Phone: {phone_number} | IP: {client_ip}"
                    )
                else:
                    # create_user returned None (IntegrityError but couldn't find user)
                    # Try get_user_by_mobile one more time with fresh session
                    logger.warning("create_user returned None. Retrying get_user_by_mobile...")
                    db.expire_all()
                    user = get_user_by_mobile(db, req.mobile)
                    if user:
                        is_new_user = False
                        logger.info(
                            f"Existing user found on retry during OTP verification | "
                            f"User ID: {user.id} | Phone: {phone_number} | IP: {client_ip}"
                        )
                    else:
                        # Still not found - try final fallback search
                        logger.warning("User not found after retry. Performing final fallback search...")
                        db.expire_all()
                        from Login_module.User.user_model import User as UserModel
                        all_users = db.query(UserModel).all()
                        mobile_normalized = str(req.mobile).strip()
                        logger.info(f"Final fallback: Searching through {len(all_users)} users")
                        for u in all_users:
                            if u.mobile:
                                db_mobile_normalized = str(u.mobile).strip()
                                # Try exact match
                                if db_mobile_normalized == mobile_normalized:
                                    user = u
                                    is_new_user = False
                                    logger.info(f"Found user (ID: {user.id}) in final fallback search (exact match)")
                                    break
                                # Try last 10 digits match
                                elif len(mobile_normalized) > 10 and len(db_mobile_normalized) >= 10:
                                    if db_mobile_normalized[-10:] == mobile_normalized[-10:]:
                                        user = u
                                        is_new_user = False
                                        logger.info(f"Found user (ID: {user.id}) in final fallback search (last 10 digits)")
                                        break
            else:
                logger.info(
                    f"Existing user found during OTP verification | "
                    f"User ID: {user.id} | Phone: {phone_number} | IP: {client_ip}"
                )
        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            # If user creation fails for any reason, try one more time to get existing user
            logger.warning(f"Error getting/creating user: {e}. Trying to get existing user...")
            db.expire_all()
            user = get_user_by_mobile(db, req.mobile)
            is_new_user = False  # We only get here when fetching existing user
            if not user:
                # If still no user found, raise error
                db.rollback()
                logger.error(
                    f"OTP verification failed - Cannot get or create user | "
                    f"Phone: {phone_number} | IP: {client_ip} | Error: {str(e)}",
                    exc_info=True
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Unable to create or find user. Please try again."
                )
        
        # Ensure we have a user at this point
        if not user:
            db.rollback()
            logger.error(
                f"OTP verification failed - No user found after all attempts | "
                f"Phone: {phone_number} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to retrieve user account. Please contact support."
            )
        
        # Member transfer to independent user functionality removed
        # Members can only be switched within the same account, not transferred to become independent users

        # Create audit log for successful verification
        try:
            OTP_crud.create_otp_audit_log(
                db=db,
                event_type="VERIFIED",
                user_id=user.id,
                device_id=req.device_id,
                phone_number=phone_number,
                reason=f"OTP verified successfully. IP: {client_ip}",
                ip_address=client_ip,
                user_agent=user_agent,
                correlation_id=correlation_id
            )
        except Exception as e:
            logger.warning(f"Failed to create audit log: {e}")

        # Device & session
        ip = client_ip
        user_agent = request.headers.get("user-agent")

        try:
            session = create_device_session(
                db=db,
                user_id=user.id,
                device_id=req.device_id,
                device_platform=req.device_platform or "unknown",
                device_details=req.device_details,
                ip=ip,
                user_agent=user_agent,
                expires_in_seconds=ACCESS_TOKEN_EXPIRE_SECONDS,
                max_active_sessions=MAX_ACTIVE_SESSIONS,
                correlation_id=correlation_id
            )
        except Exception as e:
            db.rollback()
            logger.error(
                f"OTP verification failed - Database error creating device session | "
                f"User ID: {user.id} | Phone: {phone_number} | Device: {req.device_id} | "
                f"IP: {client_ip} | Error: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error: Unable to create session. {str(e)}"
            )

        # Get default member (self profile or first member) to set in token
        from Member_module.Member_model import Member
        default_member = None
        try:
            # Try to get self profile member first
            default_member = db.query(Member).filter(
                Member.user_id == user.id,
                Member.is_self_profile == True,
                Member.is_deleted == False
            ).first()
            
            # If no self profile, get the first member (oldest by created_at)
            if not default_member:
                default_member = db.query(Member).filter(
                    Member.user_id == user.id,
                    Member.is_deleted == False
                ).order_by(Member.created_at.asc()).first()
        except Exception as e:
            # Log but don't fail login if member query fails
            logger.warning(f"Error fetching default member during login: {e}")
        
        # Determine if web or mobile platform
        device_platform = session.device_platform or "unknown"
        is_web = device_platform.lower() in ["web", "desktop_web"]
        
        # Generate token family ID for refresh token rotation tracking
        token_family_id = str(uuid.uuid4())
        
        # Verify session has valid ID before creating token
        if not session or not session.id:
            db.rollback()
            logger.error(
                f"OTP verification failed - Session ID is None or session is None | "
                f"User ID: {user.id} | Session: {session} | Session ID: {session.id if session else 'None'} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Session creation failed. Please try again."
            )
        
        # Ensure session_id is a valid integer that can be converted back
        try:
            session_id_int = int(session.id)
        except (ValueError, TypeError) as e:
            db.rollback()
            logger.error(
                f"OTP verification failed - Session ID is not a valid integer | "
                f"User ID: {user.id} | Session ID: {session.id} (type: {type(session.id)}) | IP: {client_ip} | Error: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Session creation failed. Please try again."
            )
        
        # Build access token data
        access_token_data = {
            "sub": str(user.id),
            "session_id": str(session.id),  # Store as string, but ensure it's a valid integer string
            "device_platform": device_platform
        }
        
        logger.info(
            f"Creating access token | User ID: {user.id} | Session ID: {session.id} (int: {session_id_int}) | "
            f"Token data: {access_token_data} | IP: {client_ip}"
        )
        
        # Add selected_member_id if default member exists
        if default_member:
            access_token_data["selected_member_id"] = str(default_member.id)
        
        # Generate access token
        try:
            access_token = security.create_access_token(
                access_token_data,
                expires_delta=settings.ACCESS_TOKEN_EXPIRE_SECONDS
            )
        except Exception as e:
            db.rollback()
            logger.error(
                f"OTP verification failed - Error creating access token | "
                f"User ID: {user.id} | Session ID: {session.id} | IP: {client_ip} | "
                f"Error: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Something went wrong while logging you in. Please try again."
            )
        
        # Generate refresh token with token family ID
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
            logger.error(
                f"OTP verification failed - Error creating refresh token | "
                f"User ID: {user.id} | Session ID: {session.id} | IP: {client_ip} | "
                f"Error: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Something went wrong while logging you in. Please try again."
            )
        
        # Store refresh token hash in database
        from Login_module.Token.Refresh_token_crud import create_refresh_token
        from Login_module.Utils.datetime_utils import now_ist
        from datetime import timedelta
        
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
            logger.error(
                f"OTP verification failed - Error storing refresh token | "
                f"User ID: {user.id} | Session ID: {session.id} | IP: {client_ip} | "
                f"Error: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Something went wrong while logging you in. Please try again."
            )
        
        # Link token_family_id to device_session
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

        # Save FCM token to user_device_tokens if provided (push works immediately after login)
        if req.fcm_token and req.fcm_token.strip():
            try:
                upsert_device_token(db, user.id, req.fcm_token.strip())
            except Exception as fcm_err:
                logger.warning("FCM token save failed during verify-otp (user_id=%s): %s", user.id, fcm_err)

        # Audit log token creation
        from Login_module.Token.Token_audit_crud import log_token_event
        log_token_event(
            db=db,
            event_type="TOKEN_CREATED",
            user_id=user.id,
            session_id=session.id,
            token_family_id=token_family_id,
            reason=f"Tokens created after OTP verification. Platform: {device_platform}. IP: {client_ip}",
            ip_address=client_ip,
            user_agent=user_agent,
            correlation_id=correlation_id
        )
        
        logger.info(
            f"OTP verified successfully for user {user.id} from IP {client_ip} | "
            f"Platform: {device_platform} | Session ID: {session.id} | Family ID: {token_family_id} | "
            f"Access Token contains: user_id={access_token_data.get('sub')}, session_id={access_token_data.get('session_id')}"
        )
        
        # Prepare response data based on platform
        if is_web:
            # Web: Set cookies, return user info and CSRF token in body (NO tokens in JSON)
            from Login_module.Utils.csrf import generate_csrf_token_with_secret
            csrf_token = generate_csrf_token_with_secret(user.id, session.id)
            
            # Create response with cookies (web)
            from fastapi.responses import JSONResponse
            from fastapi import Response
            
            # Get mobile number (plain text) - ensure it's a string, not None
            mobile = user.mobile if user.mobile else ""
            
            # Build response data
            response_data = VerifiedData(
                user_id=user.id,
                name=user.name,
                mobile=mobile,
                email=user.email,
                csrf_token=csrf_token,
                is_new_user=is_new_user
            )
            
            response_obj = VerifyOTPResponse(
                status="success",
                message="OTP verified successfully.",
                data=response_data
            )
            
            # Set cookies in response
            cookie_domain = settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None
            cookie_secure = settings.COOKIE_SECURE
            
            # Serialize response object to dict
            try:
                response_dict = response_obj.dict() if hasattr(response_obj, 'dict') else response_obj.model_dump()
            except Exception as e:
                logger.error(f"Error serializing response_obj: {e}, using manual dict")
                response_dict = {
                    "status": "success",
                    "message": "OTP verified successfully.",
                    "data": {
                        "user_id": user.id,
                        "name": user.name,
                        "mobile": mobile,
                        "email": user.email,
                        "csrf_token": csrf_token,
                        "is_new_user": is_new_user
                    }
                }
            
            # Create JSONResponse with cookies
            response = JSONResponse(content=response_dict)
            
            # Access token cookie - path: "/", expires when access token expires
            access_token_max_age = settings.ACCESS_TOKEN_EXPIRE_SECONDS
            response.set_cookie(
                key="access_token",
                value=access_token,
                path="/",
                httponly=True,
                secure=cookie_secure,
                samesite=settings.COOKIE_SAMESITE,
                domain=cookie_domain,
                max_age=access_token_max_age
            )
            
            # Refresh token cookie - path restricted to /auth/refresh for security
            refresh_token_max_age = int(settings.REFRESH_TOKEN_EXPIRE_DAYS_WEB * 24 * 60 * 60)
            response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                path="/auth/refresh",
                httponly=True,
                secure=cookie_secure,
                samesite=settings.REFRESH_COOKIE_SAMESITE,
                domain=cookie_domain,
                max_age=refresh_token_max_age
            )
            
            logger.info(f"Returning web response for user {user.id}")
            return response
        else:
            # Mobile: Return both tokens in JSON response body (no cookies)
            # Get mobile number (plain text) - ensure it's a string, not None
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
            
            return VerifyOTPResponse(
                status="success",
                message="OTP verified successfully.",
                data=data
            )
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error during OTP verification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong while verifying your OTP. Please try again."
        )


# NOTE: /auth/logout endpoint is now in Auth_token_router.py
# Old logout endpoint removed - use /auth/logout instead (handles dual-token strategy)