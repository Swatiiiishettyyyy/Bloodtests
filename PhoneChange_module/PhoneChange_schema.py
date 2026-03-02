"""
Phone Change Request/Response Schemas
"""
from pydantic import BaseModel, Field, validator
from typing import Optional
import re
from datetime import datetime

from Login_module.Utils.phone_validation import validate_indian_mobile


class VerifyOldNumberRequest(BaseModel):
    """Request to verify old/current phone number"""
    old_phone: str = Field(..., example="9876543210", min_length=10, max_length=15)
    
    @validator('old_phone')
    def validate_phone(cls, v):
        return validate_indian_mobile(v)


class VerifyOldNumberResponse(BaseModel):
    """Response after initiating old number verification"""
    status: str
    message: str
    request_id: Optional[int] = None
    otp: Optional[str] = None  # OTP for development/testing (remove in production when SMS is integrated)
    otp_expires_in: Optional[int] = None  # seconds
    remaining_attempts: Optional[int] = None


class ConfirmOldNumberRequest(BaseModel):
    """Request to confirm old number OTP"""
    request_id: int
    otp: str = Field(..., example="1234", min_length=4, max_length=4)
    
    @validator('otp')
    def validate_otp(cls, v):
        if not re.match(r'^\d{4}$', v):
            raise ValueError('OTP must be 4 digits')
        return v


class ConfirmOldNumberResponse(BaseModel):
    """Response after confirming old number"""
    status: str
    message: str
    session_token: Optional[str] = None
    session_expires_in: Optional[int] = None  # seconds


class VerifyNewNumberRequest(BaseModel):
    """Request to verify new phone number"""
    session_token: str
    new_phone: str = Field(..., example="9876543211", min_length=10, max_length=15)
    
    @validator('new_phone')
    def validate_phone(cls, v):
        return validate_indian_mobile(v)


class VerifyNewNumberResponse(BaseModel):
    """Response after initiating new number verification"""
    status: str
    message: str
    request_id: Optional[int] = None
    otp: Optional[str] = None  # OTP for development/testing (remove in production when SMS is integrated)
    otp_expires_in: Optional[int] = None  # seconds
    remaining_attempts: Optional[int] = None


class ConfirmNewNumberRequest(BaseModel):
    """Request to confirm new number OTP and complete phone change"""
    session_token: str
    otp: str = Field(..., example="1234", min_length=4, max_length=4)
    
    @validator('otp')
    def validate_otp(cls, v):
        if not re.match(r'^\d{4}$', v):
            raise ValueError('OTP must be 4 digits')
        return v


class ConfirmNewNumberResponse(BaseModel):
    """Response after completing phone change"""
    status: str
    message: str
    new_phone: Optional[str] = None


class CancelPhoneChangeRequest(BaseModel):
    """Request to cancel phone change process"""
    session_token: Optional[str] = None  # Optional - can cancel by session token or request_id
    request_id: Optional[int] = None


class CancelPhoneChangeResponse(BaseModel):
    """Response after cancelling phone change"""
    status: str
    message: str


class ErrorResponse(BaseModel):
    """Error response structure"""
    status: str = "error"
    message: str
    error_code: Optional[str] = None
    details: Optional[dict] = None

