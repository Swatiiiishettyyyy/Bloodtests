"""
IP-based rate limiting for OTP verification endpoint.
Prevents brute force attacks from the same IP address.
"""
import redis
import logging
from typing import Optional
from config import settings

logger = logging.getLogger(__name__)

# Import will be done after otp_manager is initialized
_redis_client = None

def _get_redis_client():
    """Lazy import to avoid circular dependency"""
    global _redis_client
    if _redis_client is None:
        from Login_module.OTP import otp_manager
        _redis_client = otp_manager._redis_client
    return _redis_client

# Rate limiting configuration - use settings directly (loaded from .env via Pydantic)
VERIFY_OTP_MAX_ATTEMPTS_PER_IP = settings.VERIFY_OTP_MAX_ATTEMPTS_PER_IP
VERIFY_OTP_WINDOW_SECONDS = settings.VERIFY_OTP_WINDOW_SECONDS


def _ip_rate_limit_key(ip: str) -> str:
    """Generate Redis key for IP-based rate limiting"""
    return f"ip_rate_limit:verify_otp:{ip}"


def check_ip_rate_limit(ip: str) -> tuple[bool, int]:
    """
    Check if IP address has exceeded rate limit for OTP verification.
    Returns (is_allowed, remaining_attempts)
    """
    if not ip or ip == "unknown":
        return True, VERIFY_OTP_MAX_ATTEMPTS_PER_IP
    
    try:
        redis_client = _get_redis_client()
        key = _ip_rate_limit_key(ip)
        attempts = redis_client.get(key)
        
        if attempts is None:
            # First attempt, set counter with expiry
            redis_client.set(key, 1, ex=VERIFY_OTP_WINDOW_SECONDS)
            return True, VERIFY_OTP_MAX_ATTEMPTS_PER_IP - 1
        
        attempts = int(attempts)
        if attempts >= VERIFY_OTP_MAX_ATTEMPTS_PER_IP:
            return False, 0
        
        # Increment counter
        redis_client.incr(key)
        remaining = VERIFY_OTP_MAX_ATTEMPTS_PER_IP - attempts - 1
        return True, max(0, remaining)
    except Exception as e:
        logger.error(f"Redis error checking IP rate limit: {e}")
        # Fail closed for security - deny if Redis is down
        return False, 0


def get_client_ip(request) -> str:
    """Extract client IP address from request"""
    # Check for forwarded IP (behind proxy/load balancer)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain
        return forwarded_for.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Fallback to direct client IP
    if request.client:
        return request.client.host
    
    return "unknown"


# Refresh token rate limiting configuration - use settings directly (loaded from .env via Pydantic)
REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION = settings.REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION
REFRESH_TOKEN_WINDOW_SECONDS = settings.REFRESH_TOKEN_WINDOW_SECONDS
REFRESH_TOKEN_MAX_FAILED_ATTEMPTS = settings.REFRESH_TOKEN_MAX_FAILED_ATTEMPTS


def _refresh_rate_limit_key(session_id: int) -> str:
    """Generate Redis key for session-based refresh token rate limiting"""
    return f"refresh_rate_limit:session:{session_id}"


def _refresh_failed_attempts_key(session_id: int) -> str:
    """Generate Redis key for tracking failed refresh attempts"""
    return f"refresh_failed_attempts:session:{session_id}"


def check_refresh_rate_limit(session_id: int) -> tuple[bool, int]:
    """
    Check if session has exceeded rate limit for refresh token endpoint.
    Returns (is_allowed, remaining_attempts)
    """
    if not session_id:
        return False, 0
    
    try:
        redis_client = _get_redis_client()
        if redis_client is None:
            logger.warning(
                "Redis unavailable for refresh rate limit; allowing refresh | "
                f"Session ID: {session_id}"
            )
            return True, REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION
        key = _refresh_rate_limit_key(session_id)
        attempts = redis_client.get(key)
        
        if attempts is None:
            # First attempt, set counter with expiry
            redis_client.set(key, 1, ex=REFRESH_TOKEN_WINDOW_SECONDS)
            return True, REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION - 1
        
        attempts = int(attempts)
        if attempts >= REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION:
            return False, 0
        
        # Increment counter
        redis_client.incr(key)
        remaining = REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION - attempts - 1
        return True, max(0, remaining)
    except Exception as e:
        logger.error(f"Redis error checking refresh rate limit: {e}")
        # Do not block token refresh if Redis is unavailable; DB token validation
        # still protects reuse/expiry and prevents locking out legitimate users.
        return True, REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION


def record_failed_refresh_attempt(session_id: int) -> bool:
    """
    Record a failed refresh attempt for a session.
    Returns True if session should be blocked (max failures reached), False otherwise.
    """
    if not session_id:
        return False
    
    try:
        redis_client = _get_redis_client()
        key = _refresh_failed_attempts_key(session_id)
        attempts = redis_client.incr(key)
        
        # Set expiry if this is the first attempt
        if attempts == 1:
            redis_client.expire(key, REFRESH_TOKEN_WINDOW_SECONDS)
        
        # Check if max failures reached
        if attempts >= REFRESH_TOKEN_MAX_FAILED_ATTEMPTS:
            logger.warning(
                f"Refresh token max failed attempts reached | Session ID: {session_id} | Attempts: {attempts}"
            )
            return True
        
        return False
    except Exception as e:
        logger.error(f"Redis error recording failed refresh attempt: {e}")
        return False


def reset_failed_refresh_attempts(session_id: int) -> None:
    """
    Reset failed refresh attempts counter for a session (on successful refresh).
    """
    if not session_id:
        return
    
    try:
        redis_client = _get_redis_client()
        key = _refresh_failed_attempts_key(session_id)
        redis_client.delete(key)
    except Exception as e:
        logger.error(f"Redis error resetting failed refresh attempts: {e}")

