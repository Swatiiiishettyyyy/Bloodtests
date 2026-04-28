import secrets
from typing import Optional
from dotenv import load_dotenv
import os
import redis
import logging
from config import settings

# Load .env file (for Redis config and other non-settings variables)
load_dotenv()

# Read OTP-related environment variables - use settings directly (loaded from .env via Pydantic)
OTP_EXPIRY_SECONDS = settings.OTP_EXPIRY_SECONDS
OTP_MAX_REQUESTS_PER_HOUR = settings.OTP_MAX_REQUESTS_PER_HOUR
OTP_MAX_FAILED_ATTEMPTS = settings.OTP_MAX_FAILED_ATTEMPTS
OTP_BLOCK_DURATION_SECONDS = settings.OTP_BLOCK_DURATION_SECONDS

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_USERNAME = os.getenv("REDIS_USERNAME")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_DB = int(os.getenv("REDIS_DB", 0))

logger = logging.getLogger(__name__)

# Initialize Redis connection (lazy initialization - don't ping at startup)
_redis_client = None
_redis_available = False

def _init_redis_client():
    """Initialize Redis client (called on first use, not at module import)"""
    global _redis_client, _redis_available
    
    if _redis_client is not None:
        return _redis_client
    
    try:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            username=REDIS_USERNAME,
            password=REDIS_PASSWORD,
            db=REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
            max_connections=50,  # Connection pool size
            socket_keepalive=True
        )
        # Test connection (non-blocking - don't fail startup if Redis is down)
        try:
            _redis_client.ping()
            _redis_available = True
            logger.info(f"✅ Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
        except (redis.ConnectionError, redis.TimeoutError, Exception) as e:
            _redis_available = False
            logger.warning(f"⚠️ Redis connection test failed (app will continue): {e}")
            logger.warning("⚠️ OTP functionality will be limited while Redis is unavailable")
        return _redis_client
    except Exception as e:
        _redis_available = False
        logger.error(f"❌ Redis initialization error (app will continue): {e}")
        logger.warning("⚠️ OTP functionality will be limited while Redis is unavailable")
        # Don't raise - allow app to start without Redis
        return None

def _get_redis_client():
    """Get Redis client (lazy initialization)"""
    if _redis_client is None:
        _init_redis_client()
    return _redis_client

def _is_redis_available():
    """Check if Redis is available and connected"""
    global _redis_available
    if not _redis_available:
        # Try to reconnect
        client = _get_redis_client()
        if client:
            try:
                client.ping()
                _redis_available = True
                logger.info("✅ Redis connection restored")
            except Exception:
                _redis_available = False
    return _redis_available


def _otp_key(country_code: str, mobile: str) -> str:
    return f"otp:{country_code}:{mobile}"


def _otp_req_key(country_code: str, mobile: str) -> str:
    return f"otp_req:{country_code}:{mobile}"


def _otp_failed_key(country_code: str, mobile: str) -> str:
    """Key for tracking failed OTP attempts"""
    return f"otp_failed:{country_code}:{mobile}"


def _otp_blocked_key(country_code: str, mobile: str) -> str:
    """Key for tracking blocked users"""
    return f"otp_blocked:{country_code}:{mobile}"


def generate_otp(length: int = 4) -> str:
    # numeric OTP
    return "".join(secrets.choice("0123456789") for _ in range(length))


def store_otp(country_code: str, mobile: str, otp: str, expires_in: int = None):
    """Store OTP in Redis with TTL"""
    client = _get_redis_client()
    if not client or not _is_redis_available():
        logger.error("Cannot store OTP: Redis is not available")
        raise redis.ConnectionError("Redis is not available")
    
    try:
        key = _otp_key(country_code, mobile)
        ex = expires_in or OTP_EXPIRY_SECONDS
        client.set(key, otp, ex=ex)
    except redis.RedisError as e:
        logger.error(f"Redis error storing OTP: {e}")
        raise


def get_otp(country_code: str, mobile: str) -> Optional[str]:
    """Get OTP from Redis"""
    client = _get_redis_client()
    if not client or not _is_redis_available():
        logger.warning("Cannot get OTP: Redis is not available")
        return None
    
    try:
        return client.get(_otp_key(country_code, mobile))
    except redis.RedisError as e:
        logger.error(f"Redis error getting OTP: {e}")
        return None


def delete_otp(country_code: str, mobile: str):
    """Delete OTP from Redis"""
    client = _get_redis_client()
    if not client or not _is_redis_available():
        logger.warning("Cannot delete OTP: Redis is not available")
        return
    
    try:
        client.delete(_otp_key(country_code, mobile))
    except redis.RedisError as e:
        logger.error(f"Redis error deleting OTP: {e}")


def can_request_otp(country_code: str, mobile: str) -> bool:
    """
    Rate limiting per-hour by storing a counter that expires in 3600 seconds.
    For security operations, we fail closed if Redis is down.
    """
    client = _get_redis_client()
    if not client or not _is_redis_available():
        # Fail closed for security - deny if Redis is down
        logger.error("Cannot rate-limit OTP requests: Redis is not available")
        return False
    try:
        client = _get_redis_client()
        if not client or not _is_redis_available():
            return False
        req_key = _otp_req_key(country_code, mobile)
        cnt = client.get(req_key)
        if cnt is None:
            # seed counter with expiry 3600
            client.set(req_key, 1, ex=3600)
            return True
        if int(cnt) >= OTP_MAX_REQUESTS_PER_HOUR:
            return False
        client.incr(req_key)
        return True
    except redis.RedisError as e:
        logger.error(f"Redis error checking OTP request limit: {e}")
        # Fail closed for security - deny if Redis is down
        return False


def get_remaining_requests(country_code: str, mobile: str) -> int:
    try:
        client = _get_redis_client()
        if not client or not _is_redis_available():
            return 0
        req_key = _otp_req_key(country_code, mobile)
        cnt = client.get(req_key)
        if cnt is None:
            return OTP_MAX_REQUESTS_PER_HOUR
        return max(0, OTP_MAX_REQUESTS_PER_HOUR - int(cnt))
    except redis.RedisError as e:
        logger.error(f"Redis error getting remaining requests: {e}")
        return OTP_MAX_REQUESTS_PER_HOUR


def is_user_blocked(country_code: str, mobile: str) -> bool:
    """Check if user is blocked due to too many failed attempts"""
    try:
        client = _get_redis_client()
        if not client or not _is_redis_available():
            return False
        block_key = _otp_blocked_key(country_code, mobile)
        blocked = client.get(block_key)
        return blocked is not None
    except redis.RedisError as e:
        logger.error(f"Redis error checking user block status: {e}")
        return False  # Fail open - don't block if Redis is down


def get_block_remaining_time(country_code: str, mobile: str) -> int:
    """Get remaining block time in seconds, or 0 if not blocked"""
    try:
        client = _get_redis_client()
        if not client or not _is_redis_available():
            return 0
        block_key = _otp_blocked_key(country_code, mobile)
        ttl = client.ttl(block_key)
        return max(0, ttl) if ttl > 0 else 0
    except redis.RedisError as e:
        logger.error(f"Redis error getting block remaining time: {e}")
        return 0


def record_failed_attempt(country_code: str, mobile: str) -> int:
    """
    Record a failed OTP attempt and return current failed count.
    If threshold reached, block the user.
    """
    client = _get_redis_client()
    if not client or not _is_redis_available():
        # Fail closed for security
        return OTP_MAX_FAILED_ATTEMPTS
    try:
        client = _get_redis_client()
        if not client or not _is_redis_available():
            return OTP_MAX_FAILED_ATTEMPTS
        failed_key = _otp_failed_key(country_code, mobile)
        failed_count = client.get(failed_key)
        
        if failed_count is None:
            # First failed attempt, set with 1 hour expiry
            client.set(failed_key, 1, ex=3600)
            failed_count = 1
        else:
            failed_count = int(failed_count) + 1
            client.set(failed_key, failed_count, ex=3600)
        
        # Block user if threshold reached
        if failed_count >= OTP_MAX_FAILED_ATTEMPTS:
            block_key = _otp_blocked_key(country_code, mobile)
            client.set(block_key, True, ex=OTP_BLOCK_DURATION_SECONDS)
            # Reset failed count after blocking
            client.delete(failed_key)
        
        return failed_count
    except redis.RedisError as e:
        logger.error(f"Redis error recording failed attempt: {e}")
        # For security, assume max attempts reached if Redis is down
        return OTP_MAX_FAILED_ATTEMPTS


def reset_failed_attempts(country_code: str, mobile: str):
    """Reset failed attempts counter (called on successful verification)"""
    try:
        client = _get_redis_client()
        if not client or not _is_redis_available():
            return
        failed_key = _otp_failed_key(country_code, mobile)
        client.delete(failed_key)
    except redis.RedisError as e:
        logger.error(f"Redis error resetting failed attempts: {e}")