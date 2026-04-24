"""
Endpoints for uploading external lab reports (PDF/JPG/PNG) to a user's account.
"""

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from deps import get_db
from Login_module.Utils.auth_user import get_current_user
from Login_module.User.user_model import User
from Login_module.Utils.datetime_utils import now_ist

from Thyrocare_module.Thyrocare_model import ThyrocareTestParameter
from .Upload_model import UploadedReport, UploadedLabResult
from .extract_utils import extract_and_parse_pdf

router = APIRouter(prefix="/upload", tags=["Upload"])

UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "uploads"


def _safe_filename(name: str) -> str:
    t = (name or "").strip()
    if not t:
        return "report"
    return "".join(ch if ch.isalnum() or ch in (" ", ".", "-", "_") else " " for ch in t).strip() or "report"


def _indicator_from_value_and_range(value_s: str | None, range_s: str | None) -> str | None:
    import re

    if not value_s or not range_s:
        return None
    try:
        value = float(str(value_s).replace(",", "").strip())
    except Exception:
        return None
    m = re.search(r"([\d.]+)\s*[-–—]\s*([\d.]+)", str(range_s))
    if not m:
        return None
    try:
        low = float(m.group(1))
        high = float(m.group(2))
    except Exception:
        return None
    if value < low:
        return "LOW"
    if value > high:
        return "HIGH"
    return "NORMAL"


def _map_organ_and_group(db: Session, description: str | None, test_code: str | None) -> tuple[Optional[str], Optional[str]]:
    """
    Map organ + group_name using ThyrocareTestParameter table.
    Primary match: parameter name == description.
    Fallback: parameter name == test_code (rare).
    """
    if description:
        row = (
            db.query(ThyrocareTestParameter.organ, ThyrocareTestParameter.group_name)
            .filter(func.lower(ThyrocareTestParameter.name) == description.lower())
            .first()
        )
        if row and (row[0] or row[1]):
            return row[0], row[1]
    if test_code:
        row = (
            db.query(ThyrocareTestParameter.organ, ThyrocareTestParameter.group_name)
            .filter(func.lower(ThyrocareTestParameter.name) == test_code.lower())
            .first()
        )
        if row and (row[0] or row[1]):
            return row[0], row[1]
    return None, None


@router.post("/report")
async def upload_report(
    file: UploadFile = File(...),
    member_id: Optional[int] = Form(None),
    lab_name: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload a report file and save it under the user's account.
    Returns a My-Reports compatible row with source='uploaded'.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=422, detail="File is required.")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    orig = _safe_filename(file.filename)
    ext = Path(orig).suffix.lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
        raise HTTPException(status_code=415, detail="Only PDF, PNG, JPG files are supported.")

    key = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_ROOT / key

    # Stream to disk (avoid loading in memory)
    try:
        with dest.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    finally:
        try:
            await file.close()
        except Exception:
            pass

    row = UploadedReport(
        user_id=current_user.id,
        member_id=member_id,
        file_name=orig,
        content_type=file.content_type,
        file_path=str(dest),
        lab_name=(lab_name or "").strip() or None,
        created_at=now_ist(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    extracted_count = 0
    # If PDF, attempt Docling extraction and persist extracted parameters for compare/metrics.
    if ext == ".pdf":
        try:
            file_bytes = dest.read_bytes()
            parsed = extract_and_parse_pdf(file_bytes)
            for item in parsed:
                organ, mapped_group = _map_organ_and_group(db, item.get("description"), item.get("test_code"))
                indicator = _indicator_from_value_and_range(item.get("test_value"), item.get("normal_val"))
                db.add(
                    UploadedLabResult(
                        uploaded_report_id=row.id,
                        user_id=current_user.id,
                        member_id=member_id,
                        test_code=item.get("test_code"),
                        description=item.get("description"),
                        test_value=item.get("test_value"),
                        normal_val=item.get("normal_val"),
                        units=item.get("units"),
                        indicator=indicator,
                        group_name=item.get("group_name") or mapped_group,
                        organ=organ,
                        category=organ,
                        raw_text=item.get("raw_text"),
                        sample_date=row.created_at,
                        source="uploaded",
                        created_at=now_ist(),
                    )
                )
                extracted_count += 1
            db.commit()
        except Exception:
            db.rollback()

    return {
        "status": "success",
        "data": {
            "id": row.id,
            "member_id": row.member_id,
            "report_name": row.file_name,
            "lab_name": row.lab_name,
            "report_date": row.created_at.isoformat() if row.created_at else None,
            "source": "uploaded",
            "download_url": f"/upload/reports/{row.id}/download",
            "results_count": extracted_count,
        },
    }


@router.get("/reports/my-reports")
def my_uploaded_reports(
    member_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    My uploaded reports as a My-Reports compatible response:
    `data[]` rows include a `results[]` array similar to `/thyrocare/reports/my-reports`.
    This lets Compare Reports / Metrics work on uploaded PDFs.
    """
    # Get reports first (so UI list has stable rows), then attach extracted result lines per report.
    rq = db.query(UploadedReport).filter(UploadedReport.user_id == current_user.id)
    if member_id is not None:
        rq = rq.filter(UploadedReport.member_id == member_id)
    reports = rq.order_by(UploadedReport.created_at.desc()).all()

    report_ids = [r.id for r in reports]
    results_by_report: dict[int, list[dict]] = {rid: [] for rid in report_ids}
    if report_ids:
        q = (
            db.query(UploadedLabResult)
            .filter(
                UploadedLabResult.user_id == current_user.id,
                UploadedLabResult.uploaded_report_id.in_(report_ids),
            )
            .order_by(UploadedLabResult.sample_date.desc())
        )
        for r in q.all():
            results_by_report.setdefault(r.uploaded_report_id, []).append(
                {
                    "test_code": r.test_code,
                    "description": r.description,
                    "test_value": r.test_value,
                    "normal_val": r.normal_val,
                    "units": r.units,
                    "indicator": r.indicator,
                    "report_group": r.group_name,
                    "sample_date": r.sample_date.isoformat() if r.sample_date else None,
                    "category": r.category,
                    "organ": r.organ,
                    "source": "uploaded",
                }
            )

    data = [
        {
            "id": rep.id,
            "member_id": rep.member_id,
            "report_name": rep.file_name,
            "lab_name": rep.lab_name,
            "report_date": rep.created_at.isoformat() if rep.created_at else None,
            "source": "uploaded",
            "download_url": f"/upload/reports/{rep.id}/download",
            "results": results_by_report.get(rep.id, []),
        }
        for rep in reports
    ]

    return {"status": "success", "total": len(data), "data": data}


@router.get("/reports/{report_id}/download")
def download_uploaded_report(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = db.query(UploadedReport).filter(
        UploadedReport.id == report_id,
        UploadedReport.user_id == current_user.id,
    ).first()
    if not r:
        raise HTTPException(status_code=404, detail="Report not found.")

    path = Path(r.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    # Let FastAPI set correct headers; use stored content-type as a hint
    return FileResponse(
        path=str(path),
        media_type=r.content_type or "application/octet-stream",
        filename=r.file_name or f"report-{report_id}{path.suffix}",
    )

