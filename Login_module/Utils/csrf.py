"""
CSRF (Cross-Site Request Forgery) protection utilities.
Generates and validates CSRF tokens for web requests.
CSRF tokens are session-bound (no expiration) and use simplified format.
"""
import hmac
import hashlib
import logging
from typing import Optional
from config import settings

logger = logging.getLogger(__name__)


def generate_csrf_token() -> str:
    """
    Generate a new CSRF token (legacy function, kept for backward compatibility).
    Returns a simple token string.
    """
    return generate_csrf_token_with_secret(0, 0)


def generate_csrf_token_with_secret(user_id: int, session_id: int) -> str:
    """
    Generate a CSRF token signed with a secret key for validation.
    Uses HMAC-SHA256 for token signing.
    Token is session-bound (no expiration) - valid for entire session.
    
    Token format: signature (HMAC of user_id:session_id)
    """
    csrf_secret = settings.CSRF_SECRET_KEY
    
    # Create message: user_id:session_id (no expiration - session-bound)
    message = f"{user_id}:{session_id}"
    
    # Generate HMAC signature
    signature = hmac.new(
        csrf_secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Return token: just the signature (simplified format)
    return signature


def validate_csrf_token(csrf_token: str, user_id: int, session_id: int) -> bool:
    """
    Validate a CSRF token against user_id and session_id.
    Token is session-bound (no expiration check).
    Returns True if valid, False otherwise.
    
    Supports both new format (signature only) and legacy formats for backward compatibility.
    """
    if not csrf_token:
        return False
    
    try:
        csrf_secret = settings.CSRF_SECRET_KEY
        
        # Try new format first: signature only (session-bound)
        message = f"{user_id}:{session_id}"
        expected_signature = hmac.new(
            csrf_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # If token matches expected signature (new format), it's valid
        if hmac.compare_digest(csrf_token, expected_signature):
            return True
        
        # Legacy format support: check if it's a multi-part token
        parts = csrf_token.split(":")
        
        # Legacy format: random:expires_at:signature or random:signature
        if len(parts) == 3:
            # Format: random:expires_at:signature
            random_part, expires_at_str, provided_signature = parts
            
            # Parse expiration timestamp
            try:
                expires_at = int(expires_at_str)
            except ValueError:
                logger.warning(f"CSRF token has invalid expiration timestamp: {expires_at_str}")
                return False
            
            # Check expiration for legacy tokens
            import time
            current_time = int(time.time())
            if current_time > expires_at:
                logger.warning(f"CSRF token expired. Current: {current_time}, Expires: {expires_at}")
                return False
            
            # Generate expected signature with expiration (legacy format)
            message = f"{user_id}:{session_id}:{expires_at}"
            expected_signature = hmac.new(
                csrf_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(provided_signature, expected_signature)
        
        elif len(parts) == 2:
            # Legacy format: random:signature (no expiration)
            random_part, provided_signature = parts
            message = f"{user_id}:{session_id}"
            expected_signature = hmac.new(
                csrf_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(provided_signature, expected_signature)
        
        # Invalid format
        logger.warning(f"CSRF token has invalid format - expected 1, 2, or 3 parts, got {len(parts)}")
        return False
        
    except Exception as e:
        logger.warning(f"CSRF token validation error: {str(e)}")
        return False


def should_exempt_from_csrf(path: str) -> bool:
    """
    Check if a path should be exempted from CSRF validation.
    Returns True if path should be exempted (e.g., /auth/* endpoints).
    """
    # Explicitly exempt refresh endpoint (critical for token refresh flow)
    if path == "/auth/refresh" or path.startswith("/auth/refresh"):
        return True
    
    # Exempt /auth/* endpoints from CSRF check
    if path.startswith("/auth/"):
        return True
    
    # Exempt health check endpoints
    if path in ["/health", "/docs", "/redoc", "/openapi.json"]:
        return True
    
    # Exempt newsletter endpoints (works for anonymous users)
    if path.startswith("/newsletter/"):
        return True

    # Exempt enquiry form submission (public form, no auth)
    if path.startswith("/enquiry"):
        return True
    
    # Exempt product endpoints (read-only or public access)
    if path.startswith("/products") or path.startswith("/product/"):
        return True
    
    # Exempt category endpoints (read-only or public access)
    if path.startswith("/categories") or path.startswith("/category/"):
        return True
    
    # Exempt banner endpoints (read-only or public access)
    if path.startswith("/banners") or path.startswith("/banner/"):
        return True
    
    # Exempt location endpoints (read-only or public access)
    if path.startswith("/location/") or path.startswith("/api/v1/location/"):
        return True
    
    # Exempt tracking endpoints (analytics/tracking endpoint, no CSRF required)
    if path.startswith("/api/tracking/"):
        return True

    # Exempt UTM landing tracking (anonymous / pre-auth)
    if path.startswith("/api/utm-tracking"):
        return True
    
    # Exempt order status endpoints (typically used by admin/lab technicians)
    if "/status" in path and ("/order" in path or path.startswith("/order")):
        return True
    
    # Exempt webhook endpoints (called by external services, no CSRF needed)
    if "/webhook" in path or path.endswith("/webhook"):
        return True
    
    return False

