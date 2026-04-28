"""
Refresh Token CRUD operations - Manages refresh token storage, rotation, and revocation.
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from typing import Optional, List
from datetime import datetime, timedelta
import uuid
import logging

from Login_module.Utils.datetime_utils import now_ist
from Login_module.Utils import Security as security
from .Refresh_token_model import RefreshToken
from Login_module.Device.Device_session_model import DeviceSession
from Login_module.Device.Device_session_audit_crud import create_session_audit_log

logger = logging.getLogger(__name__)


def create_refresh_token(
    db: Session,
    user_id: int,
    session_id: int,
    token_family_id: str,
    refresh_token: str,
    expires_at: datetime,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> RefreshToken:
    """
    Create a new refresh token record with hashed token.
    """
    token_hash = security.hash_value(refresh_token)
    
    refresh_token_record = RefreshToken(
        id=str(uuid.uuid4()),
        user_id=user_id,
        session_id=session_id,
        token_family_id=token_family_id,
        token_hash=token_hash,
        expires_at=expires_at,
        is_revoked=False,
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    db.add(refresh_token_record)
    db.commit()
    db.refresh(refresh_token_record)
    
    return refresh_token_record


def get_refresh_token_by_hash(
    db: Session,
    token_hash: str
) -> Optional[RefreshToken]:
    """
    Get refresh token record by token hash.
    """
    return db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash,
        RefreshToken.is_revoked == False
    ).first()


def revoke_refresh_token(
    db: Session,
    token_hash: str,
    reason: str = "Token rotation"
) -> bool:
    """
    Revoke a single refresh token by hash.
    Returns True if token was found and revoked, False otherwise.
    """
    refresh_token = get_refresh_token_by_hash(db, token_hash)
    if not refresh_token:
        return False
    
    refresh_token.is_revoked = True
    refresh_token.revoked_at = now_ist()
    db.commit()
    
    logger.info(
        f"Refresh token revoked | "
        f"User ID: {refresh_token.user_id} | Session ID: {refresh_token.session_id} | "
        f"Family ID: {refresh_token.token_family_id} | Reason: {reason}"
    )
    
    return True


def revoke_token_family(
    db: Session,
    token_family_id: str,
    reason: str = "Token reuse detected"
) -> int:
    """
    Revoke all refresh tokens in a token family.
    Returns the number of tokens revoked.
    """
    tokens = db.query(RefreshToken).filter(
        RefreshToken.token_family_id == token_family_id,
        RefreshToken.is_revoked == False
    ).all()
    
    revoked_count = 0
    for token in tokens:
        token.is_revoked = True
        token.revoked_at = now_ist()
        revoked_count += 1
    
    if revoked_count > 0:
        db.commit()
        logger.warning(
            f"Token family revoked - {revoked_count} token(s) | "
            f"Family ID: {token_family_id} | Reason: {reason}"
        )
    
    return revoked_count


def revoke_all_user_token_families(
    db: Session,
    user_id: int,
    reason: str = "User logout all"
) -> int:
    """
    Revoke all refresh token families for a user.
    Returns the number of token families (sessions) affected.
    """
    # Get all unique token family IDs for this user
    families = db.query(RefreshToken.token_family_id).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.is_revoked == False
    ).distinct().all()
    
    revoked_families = 0
    for (family_id,) in families:
        if family_id:
            count = revoke_token_family(db, family_id, reason)
            if count > 0:
                revoked_families += 1
    
    return revoked_families


def get_refresh_token_by_family_and_hash(
    db: Session,
    token_family_id: str,
    token_hash: str
) -> Optional[RefreshToken]:
    """
    Get refresh token by family ID and hash.
    Used for token validation during refresh.
    """
    return db.query(RefreshToken).filter(
        RefreshToken.token_family_id == token_family_id,
        RefreshToken.token_hash == token_hash,
        RefreshToken.is_revoked == False,
        RefreshToken.expires_at > now_ist()
    ).first()


def is_token_family_revoked(
    db: Session,
    token_family_id: str
) -> bool:
    """
    Check if a token family has been revoked (token reuse detection).
    
    A token family is considered revoked if ANY token in it was revoked.
    This indicates that token rotation occurred, making all previous tokens
    in the family invalid (reuse detection).
    
    Returns True if the family has been revoked (rotation occurred),
    False if the family is still active (no rotation yet).
    """
    all_tokens = db.query(RefreshToken).filter(
        RefreshToken.token_family_id == token_family_id
    ).all()
    
    if not all_tokens:
        return False
    
    # Family is revoked if ANY token is revoked (rotation occurred)
    # This detects reuse: if an old token is used after rotation,
    # at least one token in the family will be revoked
    any_revoked = any(token.is_revoked for token in all_tokens)
    return any_revoked


def has_active_refresh_token_in_family(
    db: Session,
    token_family_id: str
) -> bool:
    """
    Check if there is any non-revoked, unexpired refresh token in a family.
    Used to distinguish between true token reuse (old token used while a newer
    one is still active) vs a completely expired/cleaned-up family.
    """
    active = db.query(RefreshToken).filter(
        RefreshToken.token_family_id == token_family_id,
        RefreshToken.is_revoked == False,
        RefreshToken.expires_at > now_ist()
    ).first()
    return active is not None


def cleanup_expired_tokens(db: Session) -> int:
    """
    Clean up expired refresh tokens (optional maintenance task).
    Returns the number of tokens cleaned up.
    """
    expired_tokens = db.query(RefreshToken).filter(
        RefreshToken.expires_at < now_ist(),
        RefreshToken.is_revoked == False
    ).all()
    
    count = len(expired_tokens)
    for token in expired_tokens:
        token.is_revoked = True
        token.revoked_at = now_ist()
    
    if count > 0:
        db.commit()
        logger.info(f"Cleaned up {count} expired refresh token(s)")
    
    return count

