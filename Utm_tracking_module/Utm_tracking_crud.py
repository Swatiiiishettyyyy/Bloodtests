"""
CRUD helpers for utm_tracking.
"""
from typing import Optional

from datetime import datetime, timedelta, UTC

from sqlalchemy import update
from sqlalchemy.orm import Session

from .Utm_tracking_model import UtmTracking


def create_utm_tracking_row(
    db: Session,
    *,
    fingerprint: str,
    landing_url: str,
    user_id: Optional[int] = None,
    phone: Optional[str] = None,
    utm_source: Optional[str] = None,
    utm_medium: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    utm_term: Optional[str] = None,
    utm_content: Optional[str] = None,
) -> UtmTracking:
    if user_id is not None and int(user_id) <= 0:
        user_id = None

    # De-dupe: repeated frontend calls can create many identical rows.
    # If the same (fingerprint + landing_url + utm fields + user_id) arrives again shortly,
    # reuse the most recent row instead of inserting a new one.
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    existing = (
        db.query(UtmTracking)
        .filter(
            UtmTracking.fingerprint == fingerprint,
            UtmTracking.landing_url == landing_url,
            UtmTracking.user_id.is_(None) if user_id is None else (UtmTracking.user_id == user_id),
            UtmTracking.utm_source.is_(None) if utm_source is None else (UtmTracking.utm_source == utm_source),
            UtmTracking.utm_medium.is_(None) if utm_medium is None else (UtmTracking.utm_medium == utm_medium),
            UtmTracking.utm_campaign.is_(None) if utm_campaign is None else (UtmTracking.utm_campaign == utm_campaign),
            UtmTracking.utm_term.is_(None) if utm_term is None else (UtmTracking.utm_term == utm_term),
            UtmTracking.utm_content.is_(None) if utm_content is None else (UtmTracking.utm_content == utm_content),
            UtmTracking.created_at >= cutoff,
        )
        .order_by(UtmTracking.created_at.desc())
        .first()
    )
    if existing:
        return existing

    row = UtmTracking(
        fingerprint=fingerprint,
        landing_url=landing_url,
        user_id=user_id,
        phone=phone,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        utm_term=utm_term,
        utm_content=utm_content,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def link_utm_rows_for_new_user(
    db: Session,
    fingerprint: str,
    user_id: int,
    phone: Optional[str],
) -> int:
    """
    Attach user_id and phone to all anonymous rows for this fingerprint.
    Intended to be called only after OTP verify when is_new_user is True.
    """
    if not fingerprint or not str(fingerprint).strip():
        return 0
    fp = str(fingerprint).strip()
    stmt = (
        update(UtmTracking)
        .where(
            UtmTracking.fingerprint == fp,
            UtmTracking.user_id.is_(None),
        )
        .values(user_id=user_id, phone=phone)
    )
    result = db.execute(stmt)
    rc = result.rowcount
    return int(rc) if rc is not None else 0
