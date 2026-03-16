jimport logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional

from .Notification_model import Notification, UserDeviceToken
from Login_module.User.user_model import User

logger = logging.getLogger(__name__)


def upsert_device_token(db: Session, user_id: int, device_token: str) -> UserDeviceToken:
    """
    Insert or update device token for user.
    If token already exists (globally), update its user_id and updated_at (token reassigned).
    Otherwise insert new row. One user can have multiple tokens (multiple rows per user).
    """
    token = device_token.strip()
    existing = db.query(UserDeviceToken).filter(UserDeviceToken.device_token == token).first()
    if existing:
        existing.user_id = user_id
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing
    try:
        row = UserDeviceToken(user_id=user_id, device_token=token)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        existing = db.query(UserDeviceToken).filter(UserDeviceToken.device_token == token).first()
        if existing:
            existing.user_id = user_id
            db.commit()
            db.refresh(existing)
            return existing
        raise


def create_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    type: Optional[str] = None,
) -> Notification:
    """Insert a notification and return the model."""
    row = Notification(
        user_id=user_id,
        title=title,
        message=message,
        type=type,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_notifications(
    db: Session,
    user_id: int,
    limit: Optional[int] = None,
    unread_only: bool = False,
) -> list[Notification]:
    """List notifications for user, newest first. Optional limit and is_read filter."""
    q = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.is_read == False)
    q = q.order_by(Notification.created_at.desc())
    if limit is not None:
        q = q.limit(limit)
    return list(q.all())


def get_device_tokens_for_user(db: Session, user_id: int) -> list[str]:
    """Return list of FCM device token strings for the user."""
    rows = db.query(UserDeviceToken).filter(UserDeviceToken.user_id == user_id).all()
    return [r.device_token for r in rows]


def delete_device_tokens_by_value(db: Session, tokens: list[str]) -> int:
    """Delete rows from user_device_tokens where device_token is in the given list. Returns count deleted."""
    if not tokens:
        return 0
    deleted = db.query(UserDeviceToken).filter(UserDeviceToken.device_token.in_(tokens)).delete(synchronize_session=False)
    db.commit()
    return deleted


def mark_notification_read(db: Session, notification_id: int, user_id: int) -> Optional[Notification]:
    """Mark a notification as read if it belongs to the user. Return the notification or None."""
    row = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user_id,
    ).first()
    if not row:
        return None
    row.is_read = True
    db.commit()
    db.refresh(row)
    return row


def get_unread_count(db: Session, user_id: int) -> int:
    """Return count of unread notifications for the user."""
    return db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False,
    ).count()


def send_notification_to_user(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    type: Optional[str] = None,
) -> None:
    """
    Create a notification in DB and send push via FCM to the user's devices.
    Skips FCM send if user has notifications_enabled=False. Does not raise; logs errors.
    """
    print(f"[FCM DEBUG] send_notification_to_user ENTRY: user_id={user_id} title={title!r}", flush=True)
    try:
        notification = create_notification(db, user_id=user_id, title=title, message=message, type=type)
        user = db.query(User).filter(User.id == user_id).first()
        notifications_enabled_val = getattr(user, "notifications_enabled", True)
        print(f"[FCM DEBUG] send_notification_to_user called: user_id={user_id} notifications_enabled={notifications_enabled_val!r} pytype={notifications_enabled_val.__class__.__name__}", flush=True)
        logger.info(
            "send_notification_to_user: user_id=%s notifications_enabled=%r (pytype=%s)",
            user_id, notifications_enabled_val, notifications_enabled_val.__class__.__name__
        )
        if user and not notifications_enabled_val:
            print(f"[FCM DEBUG] Skipping FCM: notifications_enabled is falsy for user_id={user_id}", flush=True)
            logger.info("Skipping FCM: notifications_enabled is falsy for user_id=%s", user_id)
            return
        from . import firebase_service
        tokens = get_device_tokens_for_user(db, user_id)
        firebase_service.init_firebase()
        print(f"[FCM DEBUG] tokens={tokens} firebase_initialized={firebase_service.firebase_initialized}", flush=True)
        if not tokens:
            print(f"[FCM DEBUG] Skipping FCM: no device tokens for user_id={user_id}", flush=True)
            logger.error("Skipping FCM: no device tokens for user_id=%s", user_id)
        elif not firebase_service.firebase_initialized:
            print(f"[FCM DEBUG] Skipping FCM: Firebase not initialized for user_id={user_id}", flush=True)
            logger.error("Skipping FCM: Firebase not initialized")
        else:
            data = {"notification_id": str(notification.id), "type": type or ""}
            invalid_tokens, success_count = firebase_service.send_fcm_to_tokens(tokens=tokens, title=title, body=message, data=data)
            if success_count is not None:
                if success_count > 0:
                    logger.info("FCM send attempted for user_id=%s, delivered to %s device(s)", user_id, success_count)
                else:
                    logger.warning("FCM send attempted for user_id=%s, delivered to 0 device(s)", user_id)
            if invalid_tokens:
                from config import settings
                if settings.REMOVE_INVALID_FCM_TOKENS:
                    removed = delete_device_tokens_by_value(db, invalid_tokens)
                    logger.info("Removed %s invalid FCM token(s) for user_id=%s", removed, user_id)
                else:
                    logger.debug("Invalid FCM token(s) for user_id=%s not removed (REMOVE_INVALID_FCM_TOKENS=false)", user_id)
    except Exception as e:
        logger.error("send_notification_to_user failed (user_id=%s): %s", user_id, e, exc_info=True)
