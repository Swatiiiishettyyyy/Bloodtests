"""
Auth Token Router - Handles token refresh, logout, and logout-all endpoints.
Implements dual-token strategy with token rotation and reuse detection.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response, Cookie
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel
import logging
import uuid
from datetime import datetime, timedelta

from deps import get_db
from Login_module.Utils import security
from Login_module.Utils.rate_limiter import (
    get_client_ip, 
    check_refresh_rate_limit, 
    record_failed_refresh_attempt,
    reset_failed_refresh_attempts
)
from Login_module.Utils.datetime_utils import now_ist
from Login_module.Utils.csrf import generate_csrf_token_with_secret, should_exempt_from_csrf
from Login_module.User.user_model import User
from Login_module.Device.Device_session_crud import (
    get_device_session,
    update_last_active,
    deactivate_session
)
from Login_module.Device.Device_session_crud import get_user_active_sessions
from Login_module.Device.Device_session_model import DeviceSession
from .Refresh_token_crud import (
    create_refresh_token,
    get_refresh_token_by_hash,
    revoke_refresh_token,
    revoke_token_family,
    revoke_all_user_token_families,
    get_refresh_token_by_family_and_hash,
    has_active_refresh_token_in_family,
)
from Login_module.Token.Token_audit_crud import log_token_event
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
security_scheme = HTTPBearer(auto_error=False)  # auto_error=False for optional token


# Request/Response schemas
class RefreshTokenRequest(BaseModel):
    refresh_token: Optional[str] = None  # For mobile - token in request body


class RefreshTokenResponse(BaseModel):
    status: str = "success"
    message: str
    access_token: Optional[str] = None  # For mobile
    refresh_token: Optional[str] = None  # For mobile
    csrf_token: Optional[str] = None  # For web
    expires_in: int  # Access token expiry in seconds


class LogoutResponse(BaseModel):
    status: str = "success"
    message: str


class CSRFTokenResponse(BaseModel):
    status: str = "success"
    message: str
    csrf_token: str


def _get_refresh_token_from_request(
    request: Request,
    refresh_token_body: Optional[str] = None
) -> Optional[str]:
    """
    Extract refresh token from cookie (web) or request body (mobile).
    Returns None if not found.
    """
    # Try cookie first (web)
    refresh_token_cookie = request.cookies.get("refresh_token")
    if refresh_token_cookie:
        return refresh_token_cookie
    
    # Try request body (mobile)
    if refresh_token_body:
        return refresh_token_body
    
    return None


def _is_web_platform(device_platform: Optional[str]) -> bool:
    """Check if device platform is web-based"""
    web_platforms = ["web", "desktop_web"]
    return device_platform and device_platform.lower() in web_platforms


def _set_token_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    domain: Optional[str] = None,
    secure: bool = True,
    access_token_max_age: Optional[int] = None,
    refresh_token_max_age: Optional[int] = None
) -> None:
    """Set access and refresh token cookies for web"""
    cookie_kwargs = {
        "httponly": True,
        "secure": secure,
        "samesite": settings.COOKIE_SAMESITE
    }
    
    if domain:
        cookie_kwargs["domain"] = domain
    
    # Set access token cookie - path: "/", expires when access token expires (15 minutes)
    access_cookie_kwargs = cookie_kwargs.copy()
    if access_token_max_age is not None:
        access_cookie_kwargs["max_age"] = access_token_max_age  # 15 minutes = 900 seconds
    
    response.set_cookie(
        key="access_token",
        value=access_token,
        path="/",
        **access_cookie_kwargs
    )
    
    # Set refresh token cookie - path restricted to /auth/refresh, SameSite=strict for security
    refresh_cookie_kwargs = cookie_kwargs.copy()
    refresh_cookie_kwargs["samesite"] = settings.REFRESH_COOKIE_SAMESITE
    if refresh_token_max_age is not None:
        refresh_cookie_kwargs["max_age"] = refresh_token_max_age
    
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        path="/auth/refresh",  # Restricted path - only sent to refresh endpoint
        **refresh_cookie_kwargs
    )


def _clear_token_cookies(
    response: Response, 
    domain: Optional[str] = None,
    secure: bool = True
) -> None:
    """
    Clear access and refresh token cookies.
    Tries multiple parameter combinations to ensure cookies are deleted even if they
    were set with different parameters (handles old cookies, localhost development, etc.).
    
    Note: To delete a cookie, we must match the exact same parameters (path, domain, secure, samesite)
    that were used when setting it. We try common variations to handle edge cases.
    """
    # Get secure setting from config for localhost compatibility
    from config import settings
    cookie_secure = settings.COOKIE_SECURE if hasattr(settings, 'COOKIE_SECURE') else secure
    
    # Helper function to clear a cookie with specific parameters
    def clear_cookie(key: str, path: str, samesite: str, secure_val: bool):
        """Clear a cookie with specific parameters"""
        cookie_kwargs = {
            "path": path,
            "httponly": True,
            "secure": secure_val,
            "samesite": samesite,
            "max_age": 0
        }
        if domain:
            cookie_kwargs["domain"] = domain
        response.set_cookie(key=key, value="", **cookie_kwargs)
    
    # Clear access_token cookie using current SameSite configuration
    # Try both secure and non-secure for localhost compatibility
    clear_cookie("access_token", path="/", samesite=settings.COOKIE_SAMESITE, secure_val=True)
    clear_cookie("access_token", path="/", samesite=settings.COOKIE_SAMESITE, secure_val=False)
    
    # Clear refresh_token cookie using current SameSite configuration
    # Try correct path first, then "/" in case cookie was set with different path
    clear_cookie("refresh_token", path="/auth/refresh", samesite=settings.REFRESH_COOKIE_SAMESITE, secure_val=True)
    clear_cookie("refresh_token", path="/auth/refresh", samesite=settings.REFRESH_COOKIE_SAMESITE, secure_val=False)
    clear_cookie("refresh_token", path="/", samesite=settings.REFRESH_COOKIE_SAMESITE, secure_val=False)
    clear_cookie("refresh_token", path="/", samesite=settings.REFRESH_COOKIE_SAMESITE, secure_val=True)
    
    logger.info(
        f"Token cookies cleared (tried multiple parameter combinations) | "
        f"Domain: {domain} | Secure: {cookie_secure} (tried both True and False)"
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
def refresh_token(
    request: Request,
    response: Response,
    req_body: Optional[RefreshTokenRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Refresh access token using refresh token.
    Implements token rotation: generates new refresh token and invalidates old one.
    Detects token reuse (security incident) and revokes entire token family.
    
    For web: Extracts refresh token from cookie
    For mobile: Accepts refresh_token in request body
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())
    
    # Extract refresh token from cookie (web) or body (mobile)
    refresh_token_cookie = request.cookies.get("refresh_token")
    refresh_token_body = req_body.refresh_token if req_body else None
    
    # Log request details for debugging
    logger.info(
        f"Refresh token request | "
        f"Has cookie: {refresh_token_cookie is not None} | "
        f"Cookie value length: {len(refresh_token_cookie) if refresh_token_cookie else 0} | "
        f"Has body: {req_body is not None} | "
        f"Body refresh_token: {refresh_token_body is not None if refresh_token_body else False} | "
        f"IP: {client_ip}"
    )
    
    # Determine if this is a web or mobile request based on how token is sent
    # Cookie-first priority: Prevents edge case where web clients accidentally send both
    # Web: token comes from cookie (cookie takes priority, body ignored if both present)
    # Mobile: token comes from request body (only if no cookie exists)
    refresh_token_cookie_exists = refresh_token_cookie is not None and refresh_token_cookie.strip() != ""
    refresh_token_body_exists = refresh_token_body is not None and refresh_token_body.strip() != ""
    
    # Cookie-first priority: if cookie exists, it's web (prevents edge case)
    # Mobile: only if no cookie exists and body has token
    # If both present: cookie wins (web client, ignore accidental body token)
    is_web_request = refresh_token_cookie_exists
    is_mobile_request = refresh_token_body_exists and not refresh_token_cookie_exists
    
    # Get the actual refresh token value (cookie-first priority prevents edge case)
    # Cookie takes priority when present (web), body only if no cookie (mobile)
    refresh_token_value = None
    if refresh_token_cookie_exists:
        # Web: use token from cookie (takes priority - prevents edge case)
        refresh_token_value = refresh_token_cookie.strip()
        if refresh_token_body_exists:
            # Log warning if web client accidentally sent body token
            logger.warning(
                f"Web client sent both cookie and body token | "
                f"Using cookie (ignoring body) | IP: {client_ip}"
            )
    elif refresh_token_body_exists:
        # Mobile: use token from body (only if no cookie exists)
        refresh_token_value = refresh_token_body.strip()
    
    if not refresh_token_value:
        logger.warning(
            f"Token refresh failed - No refresh token provided | IP: {client_ip}"
        )
        # Clear cookies when no refresh token provided
        _clear_token_cookies(
            response,
            domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
            secure=settings.COOKIE_SECURE
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "INVALID_TOKEN", "detail": "Refresh token required"}
        )
    
    try:
        # Decode refresh token
        try:
            logger.info(f"Attempting to decode refresh token | Token length: {len(refresh_token_value)} | IP: {client_ip}")
            payload = security.decode_refresh_token(refresh_token_value)
            logger.info(f"Refresh token decoded successfully | IP: {client_ip}")
        except HTTPException as e:
            # Token expired or invalid - clear cookies and re-raise
            logger.warning(
                f"Token refresh failed - Invalid or expired refresh token | IP: {client_ip} | "
                f"Error code: {e.detail.get('error_code') if isinstance(e.detail, dict) else 'UNKNOWN'} | "
                f"Error detail: {e.detail}"
            )
            # Log to database before raising exception
            # Note: create_session_audit_log commits internally, so this should persist even if exception is raised
            try:
                log_token_event(
                    db=db,
                    event_type="TOKEN_REFRESH_FAILED",
                    user_id=None,  # Unknown user (token couldn't be decoded) - use None for nullable FK
                    session_id=None,  # Unknown session
                    reason=f"Invalid/expired token. IP: {client_ip}",
                    ip_address=client_ip,
                    user_agent=user_agent,
                    correlation_id=correlation_id
                )
            except Exception as log_error:
                # Log error but don't fail the request
                logger.error(
                    f"Failed to log token refresh failure to database | "
                    f"IP: {client_ip} | Error: {str(log_error)}",
                    exc_info=True
                )
            # Clear cookies when refresh fails (expired/invalid token)
            _clear_token_cookies(
                response,
                domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                secure=settings.COOKIE_SECURE
            )
            raise e
        
        user_id = payload.get("sub")
        session_id = payload.get("session_id")
        token_family_id = payload.get("token_family_id")
        device_platform = payload.get("device_platform", "unknown")
        
        logger.info(
            f"Token payload extracted | "
            f"User ID: {user_id} | Session ID: {session_id} | "
            f"Token Family ID: {token_family_id} | Device Platform: {device_platform} | IP: {client_ip}"
        )
        
        if not user_id or not session_id or not token_family_id:
            logger.warning(
                f"Token refresh failed - Missing required fields in token | "
                f"User ID: {user_id} | Session ID: {session_id} | Token Family ID: {token_family_id} | IP: {client_ip}"
            )
            # Clear cookies when refresh fails (invalid token structure)
            _clear_token_cookies(
                response,
                domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                secure=settings.COOKIE_SECURE
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "INVALID_TOKEN", "detail": "Invalid token structure"}
            )
        
        user_id = int(user_id)
        session_id = int(session_id)
        
        # Rate limiting check
        is_allowed, remaining = check_refresh_rate_limit(session_id)
        if not is_allowed:
            logger.warning(
                f"Token refresh rate limit exceeded | "
                f"Session ID: {session_id} | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many refresh requests. Please try again later."
            )
        
        # Validate refresh token hash in database
        token_hash = security.hash_value(refresh_token_value)
        logger.info(
            f"Looking up refresh token in database | "
            f"Token Family ID: {token_family_id} | Token Hash (first 20 chars): {token_hash[:20] if len(token_hash) > 20 else token_hash} | IP: {client_ip}"
        )
        db_refresh_token = get_refresh_token_by_family_and_hash(db, token_family_id, token_hash)
        
        if not db_refresh_token:
            # Token not found or already used - distinguish between true reuse vs generic invalid
            logger.error(
                f"Token refresh failed - Refresh token not found in database | "
                f"User ID: {user_id} | Session ID: {session_id} | Family ID: {token_family_id} | "
                f"Token Hash (first 20 chars): {token_hash[:20] if len(token_hash) > 20 else token_hash} | IP: {client_ip}"
            )
            
            # Record failed attempt
            record_failed_refresh_attempt(session_id)
            
            # Check if there is still an active (non-revoked, unexpired) token in this family
            has_active = has_active_refresh_token_in_family(db, token_family_id)
            
            if has_active:
                # SECURITY INCIDENT: Token reuse detected (old token used while a newer one is still active)
                logger.error(
                    f"SECURITY: Token reuse detected | "
                    f"User ID: {user_id} | Session ID: {session_id} | "
                    f"Family ID: {token_family_id} | IP: {client_ip}"
                )
                
                # Revoke entire token family and terminate session
                revoke_token_family(db, token_family_id, "Token reuse detected")
                
                session = get_device_session(db, session_id)
                if session and session.is_active:
                    deactivate_session(
                        db=db,
                        session_id=session_id,
                        reason="Session terminated due to token reuse",
                        ip_address=client_ip,
                        user_agent=user_agent,
                        correlation_id=correlation_id
                    )
                
                log_token_event(
                    db=db,
                    event_type="TOKEN_REUSE_DETECTED",
                    user_id=user_id,
                    session_id=session_id,
                    token_family_id=token_family_id,
                    reason=f"Token reuse detected. IP: {client_ip}",
                    ip_address=client_ip,
                    user_agent=user_agent,
                    correlation_id=correlation_id
                )
                
                # Clear cookies when token reuse detected (security incident)
                _clear_token_cookies(
                    response,
                    domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                    secure=settings.COOKIE_SECURE
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error_code": "TOKEN_REUSE_DETECTED", "detail": "Security incident detected. Please log in again."}
                )
            else:
                # No active tokens in this family - treat as generic invalid/expired token
                log_token_event(
                    db=db,
                    event_type="TOKEN_REFRESH_FAILED",
                    user_id=user_id,
                    session_id=session_id,
                    token_family_id=token_family_id,
                    reason=f"Token not found in database (no active tokens in family). IP: {client_ip}",
                    ip_address=client_ip,
                    user_agent=user_agent,
                    correlation_id=correlation_id
                )
                
                # Clear cookies when refresh token not found (invalid/revoked)
                _clear_token_cookies(
                    response,
                    domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                    secure=settings.COOKIE_SECURE
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error_code": "INVALID_TOKEN", "detail": "Invalid refresh token"}
                )
        
        # Validate session is still active
        session = get_device_session(db, session_id)
        if not session or not session.is_active:
            logger.warning(
                f"Token refresh failed - Session inactive | "
                f"User ID: {user_id} | Session ID: {session_id} | IP: {client_ip}"
            )
            # Clear cookies when session is inactive
            _clear_token_cookies(
                response,
                domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                secure=settings.COOKIE_SECURE
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "SESSION_INACTIVE", "detail": "Session is inactive. Please log in again."}
            )
        
        # Validate user exists and is active
        from Login_module.User.user_session_crud import get_user_by_id
        user = get_user_by_id(db, user_id)
        if not user or not user.is_active:
            logger.warning(
                f"Token refresh failed - User not found or inactive | "
                f"User ID: {user_id} | IP: {client_ip}"
            )
            # Clear cookies when user is inactive
            _clear_token_cookies(
                response,
                domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                secure=settings.COOKIE_SECURE
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "USER_INACTIVE", "detail": "User account is inactive"}
            )
        
        # Check absolute session lifetime (prevents indefinite sessions)
        # Session must not exceed MAX_SESSION_LIFETIME_DAYS from creation time
        from Login_module.Utils.datetime_utils import to_ist
        session_created_at = to_ist(session.created_at) if session.created_at else None
        if session_created_at:
            session_age = now_ist() - session_created_at
            max_session_lifetime = timedelta(days=settings.MAX_SESSION_LIFETIME_DAYS)
            
            if session_age > max_session_lifetime:
                # Calculate session age in minutes for better logging
                session_age_minutes = session_age.total_seconds() / 60
                max_lifetime_minutes = max_session_lifetime.total_seconds() / 60
                
                logger.warning(
                    f"Token refresh failed - Session exceeded maximum lifetime | "
                    f"User ID: {user_id} | Session ID: {session_id} | "
                    f"Session age: {session_age_minutes:.1f} minutes | Max: {max_lifetime_minutes:.1f} minutes | IP: {client_ip}"
                )
                
                # Deactivate session due to absolute expiration
                deactivate_session(
                    db=db,
                    session_id=session_id,
                    reason=f"Session exceeded maximum lifetime ({max_lifetime_minutes:.1f} minutes)",
                    ip_address=client_ip,
                    user_agent=user_agent,
                    correlation_id=correlation_id
                )
                
                # Revoke token family
                if token_family_id:
                    revoke_token_family(db, token_family_id, "Session exceeded maximum lifetime")
                
                log_token_event(
                    db=db,
                    event_type="SESSION_EXPIRED_ABSOLUTE",
                    user_id=user_id,
                    session_id=session_id,
                    token_family_id=token_family_id,
                    reason=f"Session exceeded maximum lifetime. Age: {session_age_minutes:.1f} minutes. IP: {client_ip}",
                    ip_address=client_ip,
                    user_agent=user_agent,
                    correlation_id=correlation_id
                )
                
                # Clear cookies when session exceeds maximum lifetime
                _clear_token_cookies(
                    response,
                    domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                    secure=settings.COOKIE_SECURE
                )
                
                # User-friendly error message
                if max_lifetime_minutes < 60:
                    error_message = f"Your session has expired after {max_lifetime_minutes:.0f} minutes. Please log in again."
                elif max_lifetime_minutes < 1440:
                    error_message = f"Your session has expired after {max_lifetime_minutes / 60:.1f} hours. Please log in again."
                else:
                    error_message = f"Your session has expired after {settings.MAX_SESSION_LIFETIME_DAYS:.1f} days. Please log in again."
                
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error_code": "SESSION_EXPIRED", "detail": error_message}
                )
        
        # Token rotation: Generate new refresh token with SAME family_id
        # Determine platform: Cookie-first priority (prevents edge case)
        # Web: cookie exists (takes priority even if body also present)
        # Mobile: body exists and no cookie
        logger.info(
            f"Platform detection | is_web_request: {is_web_request} | is_mobile_request: {is_mobile_request} | "
            f"device_platform from token: {device_platform} | IP: {client_ip}"
        )
        
        # Cookie-first priority: if cookie exists, it's web (prevents edge case)
        if is_web_request:
            is_web = True
            logger.info(f"Detected as WEB request (token from cookie) - returning web response format")
        elif is_mobile_request:
            is_web = False
            logger.info(f"Detected as MOBILE request (token from body, no cookie) - returning mobile response format")
        else:
            # Fallback: use device_platform from token
            is_web = _is_web_platform(device_platform)
            logger.info(f"Using fallback: device_platform={device_platform} -> is_web={is_web}")
        
        refresh_expiry_days = settings.REFRESH_TOKEN_EXPIRE_DAYS_WEB if is_web else settings.REFRESH_TOKEN_EXPIRE_DAYS_MOBILE
        
        # Calculate expiration time
        refresh_expires_at = now_ist() + timedelta(days=refresh_expiry_days)
        refresh_expiry_minutes = refresh_expiry_days * 60 * 24
        
        logger.debug(
            f"Creating refresh token | "
            f"Platform: {device_platform} | "
            f"Expiry days: {refresh_expiry_days} | "
            f"Expiry minutes: {refresh_expiry_minutes:.2f} | "
            f"Expires at: {refresh_expires_at} | "
            f"Current time: {now_ist()}"
        )
        
        # Generate new refresh token with same family_id
        new_refresh_token_data = {
            "sub": str(user_id),
            "session_id": str(session_id),
            "token_family_id": token_family_id,  # Same family ID
            "device_platform": device_platform
        }
        new_refresh_token = security.create_refresh_token(
            new_refresh_token_data,
            expires_delta_days=refresh_expiry_days
        )
        
        # Invalidate old refresh token
        revoke_refresh_token(db, token_hash, "Token rotated")
        
        # Create new refresh token record
        create_refresh_token(
            db=db,
            user_id=user_id,
            session_id=session_id,
            token_family_id=token_family_id,  # Same family ID
            refresh_token=new_refresh_token,
            expires_at=refresh_expires_at,
            ip_address=client_ip,
            user_agent=user_agent
        )
        
        # Update session refresh_token_family_id if not set
        if not session.refresh_token_family_id:
            session.refresh_token_family_id = token_family_id
            db.commit()
        
        # Generate new access token
        selected_member_id = payload.get("selected_member_id")
        access_token_data = {
            "sub": str(user_id),
            "session_id": str(session_id),
            "device_platform": device_platform
        }
        if selected_member_id:
            # Preserve currently selected member across refresh so user stays
            # on the same profile after token rotation.
            access_token_data["selected_member_id"] = str(selected_member_id)
        
        new_access_token = security.create_access_token(
            access_token_data,
            expires_delta=settings.ACCESS_TOKEN_EXPIRE_SECONDS
        )
        
        # Update session last_active
        update_last_active(db, session_id)
        
        # Reset failed attempts on successful refresh
        reset_failed_refresh_attempts(session_id)
        
        # Audit log
        log_token_event(
            db=db,
            event_type="TOKEN_REFRESHED",
            user_id=user_id,
            session_id=session_id,
            token_family_id=token_family_id,
            reason=f"Token refreshed successfully. IP: {client_ip}",
            ip_address=client_ip,
            user_agent=user_agent,
            correlation_id=correlation_id
        )
        
        logger.info(
            f"Token refreshed successfully | "
            f"User ID: {user_id} | Session ID: {session_id} | Platform: {device_platform} | IP: {client_ip}"
        )
        
        # Handle response based on platform
        # Cookie-first priority: prevents edge case where web clients send both
        # Web: cookie (ignores body if both present), Mobile: body (only if no cookie)
        if is_web:
            # Web: Set cookies, return CSRF token in body
            # Calculate max_age: access token = 15 minutes (900 seconds), refresh token = 7 days (604800 seconds)
            access_token_max_age = settings.ACCESS_TOKEN_EXPIRE_SECONDS  # 900 seconds (15 minutes)
            refresh_token_max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS_WEB * 24 * 60 * 60  # 7 days in seconds
            
            _set_token_cookies(
                response,
                new_access_token,
                new_refresh_token,
                domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
                secure=settings.COOKIE_SECURE,
                access_token_max_age=access_token_max_age,
                refresh_token_max_age=refresh_token_max_age
            )
            
            csrf_token = generate_csrf_token_with_secret(user_id, session_id)
            
            logger.info(
                f"Returning WEB response format | "
                f"User ID: {user_id} | Session ID: {session_id} | IP: {client_ip}"
            )
            
            # Simplified response - HTTP 200 already indicates success
            return RefreshTokenResponse(
                status="success",
                message="Token refreshed successfully",
                access_token=new_access_token,
                csrf_token=csrf_token,
                expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS
            )
        else:
            # Mobile: Return tokens in response body (no cookie, token from body)
            logger.info(
                f"Returning MOBILE response format | "
                f"User ID: {user_id} | Session ID: {session_id} | IP: {client_ip}"
            )
            return RefreshTokenResponse(
                status="success",
                message="Token refreshed successfully",
                access_token=new_access_token,
                refresh_token=new_refresh_token,
                expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Token refresh failed - Unexpected error | IP: {client_ip} | Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during token refresh"
        )


@router.post("/logout", response_model=LogoutResponse)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    """
    Logout from current session.
    Revokes refresh token family and deactivates session.
    For web: Clears cookies
    For mobile: Client should discard tokens
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())
    
    # Try to get session from access token (cookie for web, header for mobile)
    access_token = None
    session_id = None
    user_id = None
    token_family_id = None
    
    # Try cookie first (web)
    access_token_cookie = request.cookies.get("access_token")
    if access_token_cookie:
        access_token = access_token_cookie
    else:
        # Try Authorization header (mobile)
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            access_token = auth_header.split(" ")[1]
    
    if access_token:
        try:
            payload = security.decode_access_token(access_token)
            session_id = payload.get("session_id")
            user_id = payload.get("sub")
            if session_id:
                session_id = int(session_id)
            if user_id:
                user_id = int(user_id)
        except HTTPException:
            # Token invalid/expired - still proceed with logout (clear cookies)
            pass
    
    # If we have session_id, get session and revoke token family
    if session_id:
        session = get_device_session(db, session_id)
        if session:
            token_family_id = session.refresh_token_family_id
            
            # Revoke refresh token family
            if token_family_id:
                revoked_count = revoke_token_family(db, token_family_id, "User logout")
                if revoked_count > 0:
                    log_token_event(
                        db=db,
                        event_type="TOKEN_FAMILY_REVOKED",
                        user_id=session.user_id,
                        session_id=session_id,
                        token_family_id=token_family_id,
                        reason="User logout",
                        ip_address=client_ip,
                        user_agent=user_agent,
                        correlation_id=correlation_id
                    )
            
            # Deactivate session
            deactivate_session(
                db=db,
                session_id=session_id,
                reason="User logout",
                ip_address=client_ip,
                user_agent=user_agent,
                correlation_id=correlation_id
            )
            
            logger.info(
                f"User logged out | User ID: {session.user_id} | Session ID: {session_id} | IP: {client_ip}"
            )
    
    # Clear cookies for web
    # Use same secure setting as when setting cookies
    _clear_token_cookies(
        response,
        domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
        secure=settings.COOKIE_SECURE
    )
    
    return LogoutResponse(
        status="success",
        message="Logged out successfully"
    )


@router.post("/logout-all", response_model=LogoutResponse)
def logout_all(
    request: Request,
    response: Response,
    current_user: User = Depends(lambda: None),  # Will be handled manually
    db: Session = Depends(get_db)
):
    """
    Logout from all sessions.
    Revokes all refresh token families and deactivates all sessions for the user.
    For web: Clears cookies
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    correlation_id = str(uuid.uuid4())
    
    # Try to get user from access token
    user_id = None
    access_token = None
    
    # Try cookie first (web)
    access_token_cookie = request.cookies.get("access_token")
    if access_token_cookie:
        access_token = access_token_cookie
    else:
        # Try Authorization header (mobile)
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            access_token = auth_header.split(" ")[1]
    
    if access_token:
        try:
            payload = security.decode_access_token(access_token)
            user_id = payload.get("sub")
            if user_id:
                user_id = int(user_id)
        except HTTPException:
            # Token invalid/expired - cannot proceed without user ID
            logger.warning(
                f"Logout all failed - Invalid token | IP: {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
    
    if not user_id:
        logger.warning(
            f"Logout all failed - User ID not found | IP: {client_ip}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # Revoke all token families for user
    revoked_families = revoke_all_user_token_families(
        db=db,
        user_id=user_id,
        reason="User logout all"
    )
    
    # Deactivate all sessions for user
    active_sessions = get_user_active_sessions(db, user_id)
    
    for session in active_sessions:
        deactivate_session(
            db=db,
            session_id=session.id,
            reason="All sessions logged out by user",
            ip_address=client_ip,
            user_agent=user_agent,
            correlation_id=correlation_id
        )
    
    logger.info(
        f"User logged out from all sessions | "
        f"User ID: {user_id} | Sessions: {len(active_sessions)} | Families: {revoked_families} | IP: {client_ip}"
    )
    
    # Clear cookies for web
    # Use same secure setting as when setting cookies
    _clear_token_cookies(
        response,
        domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
        secure=settings.COOKIE_SECURE
    )
    
    return LogoutResponse(
        status="success",
        message=f"Logged out from {len(active_sessions)} session(s) successfully"
    )


@router.get("/csrf-token", response_model=CSRFTokenResponse)
def get_csrf_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    access_token_cookie: Optional[str] = Cookie(None, alias="access_token"),
    db: Session = Depends(get_db)
):
    """
    Get CSRF token for the current authenticated user.
    This endpoint is useful for Swagger/testing to get a valid CSRF token.
    
    Requires authentication via:
    - Cookie (web): access_token cookie
    - Header (mobile): Authorization: Bearer <token>
    
    Returns a CSRF token that can be used in X-CSRF-Token header for state-changing operations.
    """
    # Extract token from cookie (web) or Authorization header (mobile)
    token = None
    
    if access_token_cookie:
        token = access_token_cookie
    elif credentials and credentials.credentials:
        token = credentials.credentials
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "INVALID_TOKEN", "detail": "Access token required"}
        )
    
    # Decode token to get user_id and session_id
    try:
        payload, is_expired, is_invalid = security.decode_access_token_with_expiry_check(token)
        
        # Reject invalid OR expired tokens
        if is_invalid or is_expired or not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "INVALID_TOKEN", "detail": "Invalid or expired access token"}
            )
        
        user_id = payload.get("sub")
        session_id = payload.get("session_id")
        
        if not user_id or not session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "INVALID_TOKEN", "detail": "Token missing required fields"}
            )
        
        # Generate CSRF token
        csrf_token = generate_csrf_token_with_secret(int(user_id), int(session_id))
        
        return CSRFTokenResponse(
            status="success",
            message="CSRF token generated successfully",
            csrf_token=csrf_token
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating CSRF token: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "INTERNAL_ERROR", "detail": "Failed to generate CSRF token"}
        )

