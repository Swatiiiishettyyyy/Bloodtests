from pydantic import BaseModel, Field, validator
from typing import Optional
import re

from Login_module.Utils.phone_validation import validate_indian_mobile


# Request schemas
class SendOTPRequest(BaseModel):
    country_code: str = Field(..., example="+91", min_length=1, max_length=5)
    mobile: str = Field(..., example="9876543210", min_length=10, max_length=15)
    purpose: str = Field(..., example="login", max_length=50)
    
    @validator('country_code')
    def validate_country_code(cls, v):
        if not re.match(r'^\+?\d{1,4}$', v):
            raise ValueError('Invalid country code format')
        return v
    
    @validator('mobile')
    def validate_mobile(cls, v):
        return validate_indian_mobile(v)


class VerifyOTPRequest(BaseModel):
    country_code: str = Field(..., example="+91", min_length=1, max_length=5)
    mobile: str = Field(..., example="9876543210", min_length=10, max_length=15)
    otp: str = Field(..., example="1234", min_length=4, max_length=4)
    device_id: str = Field(..., example="device-uuid-or-imei", min_length=1, max_length=255)
    device_platform: str = Field(..., example="web", max_length=50)  # web/mobile/ios/android
    device_details: str = Field(..., example='{"browser":"Chrome", "version":"..."}', max_length=1000)
    fcm_token: Optional[str] = Field(None, max_length=255, description="FCM device token for push notifications")
    utm_fingerprint: Optional[str] = Field(
        None,
        max_length=255,
        description="Same fingerprint as POST /api/utm-tracking to link anonymous rows when is_new_user",
    )
    
    @validator('country_code')
    def validate_country_code(cls, v):
        if not re.match(r'^\+?\d{1,4}$', v):
            raise ValueError('Invalid country code format')
        return v
    
    @validator('mobile')
    def validate_mobile(cls, v):
        return validate_indian_mobile(v)
    
    @validator('otp')
    def validate_otp(cls, v):
        if not re.match(r'^\d{4}$', v):
            raise ValueError('OTP must be exactly 4 digits')
        return v
    
    @validator('device_id')
    def validate_device_id(cls, v):
        if len(v.strip()) == 0:
            raise ValueError('Device ID cannot be empty')
        return v.strip()

    @validator('utm_fingerprint')
    def validate_utm_fingerprint(cls, v):
        if v is None:
            return None
        s = v.strip()
        return s if s else None


# Response schemas
class OTPData(BaseModel):
    mobile: str
    expires_in: int
    purpose: Optional[str]


class SendOTPResponse(BaseModel):
    status: str = "success"
    message: str
    data: OTPData


class VerifiedData(BaseModel):
    user_id: int
    name: Optional[str]
    mobile: str
    email: Optional[str]
    access_token: Optional[str] = None  # For mobile only - web uses cookies
    refresh_token: Optional[str] = None  # For mobile only - web uses cookies
    token_type: Optional[str] = None  # For mobile only
    expires_in: Optional[int] = None  # For mobile only
    csrf_token: Optional[str] = None  # For web only
    is_new_user: bool = False  # True if user was just created during this OTP verification


class VerifyOTPResponse(BaseModel):
    status: str = "success"
    message: str
    data: VerifiedData