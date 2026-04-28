"""
Compatibility shim.

Some modules import `Login_module.Utils.security` (lowercase), but the implementation
is in `Security.py` (capital S). On case-sensitive environments this breaks.

Export the public API from `Security.py` under the expected lowercase module name.
"""

from .Security import *  # noqa: F401,F403

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
import jwt
from fastapi import HTTPException
import hashlib
from config import settings

# Read secret and algorithm from settings (loaded from .env via Pydantic)
SECRET_KEY = settings.SECRET_KEY
if not SECRET_KEY:
    raise ValueError("SECRET_KEY is missing in .env file")

ALGORITHM = settings.ALGORITHM

# Token expiry - use settings directly (loaded from .env via Pydantic)
ACCESS_TOKEN_EXPIRE_SECONDS = settings.ACCESS_TOKEN_EXPIRE_SECONDS
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

REFRESH_TOKEN_EXPIRE_DAYS_WEB = settings.REFRESH_TOKEN_EXPIRE_DAYS_WEB
REFRESH_TOKEN_EXPIRE_DAYS_MOBILE = settings.REFRESH_TOKEN_EXPIRE_DAYS_MOBILE


def create_access_token(data: Dict[str, Any], expires_delta: int = None) -> str:
    """
    Creates a JWT access token with expiration timestamp.
    """
    to_encode = data.copy()
    from Login_module.Utils.datetime_utils import now_ist
    expire_datetime = now_ist() + timedelta(
        seconds=(expires_delta or ACCESS_TOKEN_EXPIRE_SECONDS)
    )
    # Convert datetime to Unix timestamp (seconds since epoch) for JWT exp claim
    # PyJWT expects exp as numeric timestamp, not datetime object
    expire_timestamp = int(expire_datetime.timestamp())
    to_encode.update({"exp": expire_timestamp})
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return token


def hash_value(value: str) -> str:
    """
    Returns SHA256 hashed string of a given plain text.
    Used for hashing OTP and user-entered OTP during verification.
    """
    return hashlib.sha256(value.encode()).hexdigest()


def decode_access_token(token: str):
    """
    Decodes and validates JWT access token.
    Raises HTTPException for invalid or expired tokens.
    """
    try:
        decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return decoded
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def decode_access_token_with_expiry_check(token: str) -> Tuple[Optional[Dict[str, Any]], bool, bool]:
    """
    Decodes JWT access token and returns token data with expiry status.
    Returns: (payload, is_expired, is_invalid)
    - If token is valid: (payload, False, False)
    - If token is expired but valid signature: (decoded_payload, True, False) - returns payload even if expired
    - If token is invalid: (None, False, True)
    """
    try:
        decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return decoded, False, False
    except jwt.ExpiredSignatureError:
        # Token expired but signature is valid - decode without expiry check to get payload
        try:
            # Decode without verifying expiry to get payload for session validation
            decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
            return decoded, True, False  # Return payload even though expired
        except jwt.InvalidTokenError:
            # Invalid signature or malformed token
            return None, False, True
        except Exception as e:
            # Any other error - treat as invalid
            return None, False, True
    except jwt.InvalidTokenError:
        return None, False, True
    except Exception as e:
        # Any other unexpected error
        return None, False, True


def create_refresh_token(data: Dict[str, Any], expires_delta_days: float) -> str:
    """
    Creates a JWT refresh token with expiration timestamp.
    expires_delta_days: Number of days until expiry (e.g., 7 for 7 days)
    """
    to_encode = data.copy()
    from Login_module.Utils.datetime_utils import now_ist
    # Use timedelta with days parameter (accepts float for fractional days)
    expire_datetime = now_ist() + timedelta(days=expires_delta_days)
    # Convert datetime to Unix timestamp (seconds since epoch) for JWT exp claim
    # PyJWT expects exp as numeric timestamp, not datetime object
    expire_timestamp = int(expire_datetime.timestamp())
    to_encode.update({"exp": expire_timestamp})
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return token


def decode_refresh_token(token: str) -> Dict[str, Any]:
    """
    Decodes and validates JWT refresh token.
    Raises HTTPException for invalid or expired tokens.
    
    Returns SESSION_EXPIRED (not TOKEN_EXPIRED) when refresh token expires,
    to distinguish from access token expiration.
    """
    import logging
    from datetime import datetime
    from Login_module.Utils.datetime_utils import now_ist
    logger = logging.getLogger(__name__)
    
    try:
        # Decode without verification first to check expiration
        unverified = jwt.decode(token, options={"verify_signature": False})
        exp_timestamp = unverified.get("exp")
        
        if exp_timestamp:
            exp_datetime = datetime.fromtimestamp(exp_timestamp, tz=now_ist().tzinfo)
            current_time = now_ist()
            time_remaining = (exp_datetime - current_time).total_seconds()
            
            logger.debug(
                f"Refresh token expiration check | "
                f"Current time: {current_time} | "
                f"Expires at: {exp_datetime} | "
                f"Time remaining: {time_remaining:.1f} seconds ({time_remaining / 60:.2f} minutes)"
            )
        
        # Now decode with full verification
        decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return decoded
    except jwt.ExpiredSignatureError as e:
        current_time = now_ist()
        logger.warning(
            f"Refresh token expired | "
            f"Current time: {current_time} | "
            f"Token was expected to be valid but expired"
        )
        raise HTTPException(status_code=401, detail={"error_code": "SESSION_EXPIRED", "detail": "Refresh token expired. Please log in again."})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail={"error_code": "INVALID_TOKEN", "detail": "Invalid refresh token"})