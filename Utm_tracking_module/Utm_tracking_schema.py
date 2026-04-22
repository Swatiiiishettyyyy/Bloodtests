"""
Pydantic schemas for UTM tracking API.
"""
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class UtmTrackingCreate(BaseModel):
    fingerprint: str = Field(..., min_length=1, max_length=255)
    landing_url: str = Field(..., min_length=1, description="Full landing page URL")
    user_id: Optional[int] = Field(None, description="Usually omitted for anonymous hits")
    phone: Optional[str] = Field(None, max_length=100)
    utm_source: Optional[str] = Field(None, max_length=255)
    utm_medium: Optional[str] = Field(None, max_length=255)
    utm_campaign: Optional[str] = Field(None, max_length=255)
    utm_term: Optional[str] = Field(None, max_length=255)
    utm_content: Optional[str] = Field(None, max_length=255)

    @field_validator("fingerprint")
    @classmethod
    def strip_fingerprint(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("fingerprint cannot be empty")
        return s

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, v: Optional[int]) -> Optional[int]:
        # Some clients send 0 to mean "anonymous". Avoid FK failures (users.id starts at 1).
        if v is None:
            return None
        if int(v) <= 0:
            return None
        return int(v)


class UtmTrackingRecordData(BaseModel):
    id: int
    created_at: Optional[str] = None


class UtmTrackingResponse(BaseModel):
    status: str = "success"
    message: str
    data: UtmTrackingRecordData
