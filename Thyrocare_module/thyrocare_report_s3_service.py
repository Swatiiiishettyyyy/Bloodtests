"""
Upload Thyrocare lab report PDFs to a dedicated S3 bucket.
Uses the same AWS credential env vars as other S3 services.
"""
import os
import logging
import re
from typing import Optional
from urllib.parse import urlparse, unquote

import boto3

logger = logging.getLogger(__name__)


def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(key, default)
    if value and isinstance(value, str):
        value = value.strip().strip('"').strip("'")
    return value


AWS_ACCESS_KEY_ID = _get_env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _get_env("AWS_SECRET_ACCESS_KEY")
AWS_REGION = _get_env("AWS_REGION", "ap-south-1")
S3_THYROCARE_REPORTS_BUCKET = _get_env("S3_THYROCARE_REPORTS_BUCKET")
S3_THYROCARE_REPORTS_PREFIX = _get_env("S3_THYROCARE_REPORTS_PREFIX", "thyrocare_reports")
S3_THYROCARE_REPORTS_BASE_URL = _get_env("S3_THYROCARE_REPORTS_BASE_URL")
# Presigned GET lifetime for private buckets (seconds). Default 1 hour.
try:
    S3_THYROCARE_REPORTS_PRESIGN_EXPIRES = int(
        (_get_env("S3_THYROCARE_REPORTS_PRESIGN_EXPIRES", "3600") or "3600").strip()
    )
except ValueError:
    S3_THYROCARE_REPORTS_PRESIGN_EXPIRES = 3600


def _safe_path_segment(value: str) -> str:
    s = (value or "").strip().upper()
    s = re.sub(r"[^A-Z0-9._-]+", "_", s)
    return s or "unknown"


class ThyrocareReportS3Service:
    def __init__(self):
        if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
            logger.warning("AWS credentials not configured. Thyrocare report S3 uploads will fail.")
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )
        self.bucket = S3_THYROCARE_REPORTS_BUCKET
        self.prefix = (S3_THYROCARE_REPORTS_PREFIX or "thyrocare_reports").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.bucket)

    def build_report_s3_key(
        self,
        member_id: Optional[int],
        patient_id: str,
        thyrocare_order_id: Optional[str] = None,
    ) -> str:
        """Same layout as upload — use when resolving keys for presign without re-uploading."""
        patient_seg = _safe_path_segment(patient_id)
        if member_id is not None:
            member_seg = str(int(member_id))
        else:
            order_seg = _safe_path_segment(thyrocare_order_id or "")
            member_seg = f"order_{order_seg}"
        path_body = f"{member_seg}/{patient_seg}/reports/report.pdf"
        return f"{self.prefix}/{path_body}" if self.prefix else path_body

    def upload_report_pdf(
        self,
        member_id: Optional[int],
        patient_id: str,
        file_content: bytes,
        thyrocare_order_id: Optional[str] = None,
    ) -> str:
        """
        Key layout: {prefix}/{member_id}/{patient_id}/reports/report.pdf
        If member_id is unknown, uses order_{thyrocare_order_id} so the path stays unique.
        Returns the S3 object key (for DB). Use presigned_get_url for private buckets.
        """
        if not self.bucket:
            raise ValueError("S3_THYROCARE_REPORTS_BUCKET not configured")

        s3_key = self.build_report_s3_key(member_id, patient_id, thyrocare_order_id)

        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=s3_key,
            Body=file_content,
            ContentType="application/pdf",
        )
        logger.info("Uploaded Thyrocare report PDF to s3://%s/%s", self.bucket, s3_key)
        return s3_key

    def presigned_get_url(self, s3_key: str, expires_in: Optional[int] = None) -> str:
        if not self.bucket:
            raise ValueError("S3_THYROCARE_REPORTS_BUCKET not configured")
        exp = expires_in if expires_in is not None else S3_THYROCARE_REPORTS_PRESIGN_EXPIRES
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": s3_key},
            ExpiresIn=exp,
        )

    @staticmethod
    def try_parse_s3_key_from_url(url: str) -> Optional[str]:
        """Best-effort: extract object key from a virtual-hosted or path-style HTTPS URL we stored earlier."""
        if not url or not isinstance(url, str):
            return None
        try:
            path = urlparse(url.strip()).path or ""
            key = unquote(path.lstrip("/"))
            return key if key else None
        except Exception:
            return None


_service: Optional[ThyrocareReportS3Service] = None


def get_thyrocare_report_s3_service() -> ThyrocareReportS3Service:
    global _service
    if _service is None:
        _service = ThyrocareReportS3Service()
    return _service
