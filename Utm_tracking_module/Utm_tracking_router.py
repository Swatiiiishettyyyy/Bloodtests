"""
UTM tracking — record landing / campaign parameters (anonymous-friendly).
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from deps import get_db
from Login_module.Utils.datetime_utils import to_ist_isoformat
from .Utm_tracking_schema import UtmTrackingCreate, UtmTrackingResponse, UtmTrackingRecordData
from .Utm_tracking_crud import create_utm_tracking_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/utm-tracking", tags=["UTM Tracking"])


@router.post(
    "",
    response_model=UtmTrackingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record UTM / landing data",
)
def record_utm_tracking(
    body: UtmTrackingCreate,
    db: Session = Depends(get_db),
):
    try:
        row = create_utm_tracking_row(
            db,
            fingerprint=body.fingerprint,
            landing_url=body.landing_url,
            user_id=body.user_id,
            phone=body.phone,
            utm_source=body.utm_source,
            utm_medium=body.utm_medium,
            utm_campaign=body.utm_campaign,
            utm_term=body.utm_term,
            utm_content=body.utm_content,
        )
        db.commit()
        created = None
        if row.created_at is not None:
            created = to_ist_isoformat(row.created_at)
        return UtmTrackingResponse(
            status="success",
            message="UTM tracking recorded.",
            data=UtmTrackingRecordData(id=row.id, created_at=created),
        )
    except SQLAlchemyError as e:
        logger.exception("UTM tracking insert failed: %s", e)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record UTM data.",
        )
