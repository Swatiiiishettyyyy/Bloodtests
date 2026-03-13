"""
Firebase Admin SDK initialization and FCM send.
Initializes once at startup (or on first use); sends push notifications via FCM.
"""
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

firebase_initialized = False


def init_firebase() -> bool:
    """
    Initialize Firebase Admin from service account JSON.
    Uses relative path first: Notification_module/Notifications.json, then project root Notifications.json.
    If neither exists, uses FIREBASE_SERVICE_ACCOUNT_PATH (e.g. for production when file is mounted elsewhere).
    Returns True if initialized, False otherwise (FCM send will be skipped).
    """
    global firebase_initialized
    if firebase_initialized:
        return True

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.warning("firebase-admin not installed; FCM push will be skipped.")
        return False

    if firebase_admin._apps:
        firebase_initialized = True
        return True

    # Prefer relative path: Notification_module/Notifications.json, then project root Notifications.json
    this_dir = Path(__file__).resolve().parent
    root = this_dir.parent
    path = None
    if os.path.isfile(this_dir / "Notifications.json"):
        path = str(this_dir / "Notifications.json")
    elif os.path.isfile(root / "Notifications.json"):
        path = str(root / "Notifications.json")
    if not path or not os.path.isfile(path):
        # Optional: production can set FIREBASE_SERVICE_ACCOUNT_PATH when file is mounted elsewhere
        from config import settings
        fallback = (settings.FIREBASE_SERVICE_ACCOUNT_PATH or "").strip()
        if fallback and os.path.isfile(fallback):
            path = fallback
        else:
            logger.warning(
                "Firebase service account file not found (tried %s and %s). Set FIREBASE_SERVICE_ACCOUNT_PATH for a custom path. FCM push will be skipped.",
                this_dir / "Notifications.json",
                root / "Notifications.json",
            )
            return False

    try:
        cred = credentials.Certificate(path)
        firebase_admin.initialize_app(cred)
        firebase_initialized = True
        logger.info("Firebase Admin initialized for FCM (push notifications enabled).")
        return True
    except Exception as e:
        logger.warning("Firebase Admin init failed: %s; FCM push will be skipped.", e)
        return False


def _is_invalid_or_unregistered_token(exc: Optional[Exception]) -> bool:
    """Return True if the FCM exception indicates token should be removed (unregistered/invalid)."""
    if exc is None:
        return False
    name = type(exc).__name__
    msg = (getattr(exc, "message", None) or str(exc)).lower()
    if "UnregisteredError" in name or "unregistered" in msg:
        return True
    if "invalid" in msg or "not a valid fcm registration" in msg or "requested entity was not found" in msg:
        return True
    return False


def send_fcm_to_tokens(
    tokens: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> Tuple[list[str], Optional[int]]:
    """
    Send FCM notification to the given device tokens.
    Does not raise; logs errors so API responses are not tied to FCM failures.
    Returns (invalid_tokens, success_count): list of token strings that are invalid/unregistered
    and should be removed from DB, and the number of successful deliveries (None if batch failed).
    """
    invalid_tokens: list[str] = []
    if not tokens:
        return (invalid_tokens, None)
    init_firebase()
    if not firebase_initialized:
        return (invalid_tokens, None)

    try:
        import firebase_admin
        from firebase_admin import messaging
    except ImportError:
        logger.warning("firebase-admin not available; skipping FCM send.")
        return (invalid_tokens, None)

    if not firebase_admin._apps:
        logger.warning("Firebase not initialized; skipping FCM send.")
        return (invalid_tokens, None)

    logger.debug(
        "FCM send: token_count=%s first_token_prefix=%s",
        len(tokens),
        (tokens[0][:20] + "..." if len(tokens[0]) > 20 else tokens[0]) if tokens else "",
    )

    # data must be string key -> string value for FCM
    data_dict: Optional[dict[str, str]] = None
    if data:
        data_dict = {k: str(v) for k, v in data.items()}

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=data_dict,
        tokens=tokens,
    )
    try:
        batch = messaging.send_each_for_multicast(message)
        print(f"[FCM DEBUG] batch result: success={batch.success_count} failure={batch.failure_count} total={len(tokens)}", flush=True)
        logger.info(
            "FCM batch: success_count=%s failure_count=%s total=%s",
            batch.success_count,
            batch.failure_count,
            len(tokens),
        )
        if batch.failure_count > 0:
            failed_indices = [i for i, r in enumerate(batch.responses) if not r.success]
            logger.warning("FCM failed token indices: %s", failed_indices)
            for i, send_response in enumerate(batch.responses):
                if not send_response.success:
                    exc = getattr(send_response, "exception", None)
                    print(f"[FCM DEBUG] token[{i}] failed: {getattr(exc, 'message', exc)}", flush=True)
                    logger.warning(
                        "FCM send failed for token index %s: %s",
                        i,
                        getattr(exc, "message", exc),
                    )
                    if i < len(tokens) and _is_invalid_or_unregistered_token(exc):
                        invalid_tokens.append(tokens[i])
        return (invalid_tokens, batch.success_count)
    except Exception as e:
        logger.error("FCM send failed (batch exception): %s", e, exc_info=True)
        return (invalid_tokens, None)
