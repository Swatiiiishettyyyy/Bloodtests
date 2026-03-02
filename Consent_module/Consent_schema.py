from typing import List, Optional
from pydantic import BaseModel, Field, validator
from datetime import datetime

from Login_module.Utils.phone_validation import validate_indian_mobile


class ConsentRecordRequest(BaseModel):
    """Request schema for recording consent (for product pages)"""
    product_id: int = Field(..., description="Product ID for which consent is given (1-17)", gt=0)
    consent_value: str = Field(..., description="Consent value: 'yes' or 'no'")
    consent_source: Optional[str] = Field(default="product", description="Source of consent (defaults to 'product')")
    
    @validator('consent_value')
    def validate_consent_value(cls, v):
        v = v.lower().strip()
        if v not in ['yes', 'no']:
            raise ValueError('consent_value must be "yes" or "no"')
        return v
    
    @validator('consent_source')
    def validate_consent_source(cls, v):
        if v is None:
            return "product"
        v = v.lower().strip()
        if v not in ['login', 'product']:
            raise ValueError('consent_source must be "login" or "product"')
        return v
    
    @validator('product_id')
    def validate_product_id(cls, v):
        # Product ID must be a positive integer
        if not isinstance(v, int) or v <= 0:
            raise ValueError('product_id must be a positive integer')
        return v


class ConsentBulkRequest(BaseModel):
    """Request schema for bulk consent (login scenario - all products)"""
    consent_value: str = Field(..., description="Consent value: 'yes' or 'no'")
    consent_source: str = Field(default="login", description="Source of consent: 'login' or 'product'")
    
    @validator('consent_value')
    def validate_consent_value(cls, v):
        v = v.lower().strip()
        if v not in ['yes', 'no']:
            raise ValueError('consent_value must be "yes" or "no"')
        return v
    
    @validator('consent_source')
    def validate_consent_source(cls, v):
        v = v.lower().strip()
        if v not in ['login', 'product']:
            raise ValueError('consent_source must be "login" or "product"')
        return v


class ManageConsentRequest(BaseModel):
    """Request schema for manage consent page"""
    product_consents: List[dict] = Field(..., description="List of product consent updates")
    
    class Config:
        json_schema_extra = {
            "example": {
                "product_consents": [
                    {"product_id": 1, "status": "yes"},
                    {"product_id": 2, "status": "no"}
                ]
            }
        }


class ConsentData(BaseModel):
    """Consent data model"""
    id: int
    user_id: int
    user_phone: str
    member_id: int
    product_id: int
    product: Optional[str] = None  # Product name
    consent_given: int
    consent_source: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConsentBulkData(BaseModel):
    """Consent data model for bulk consent (without product_id)"""
    id: int
    user_id: int
    user_phone: str
    member_id: int
    consent_given: int
    consent_source: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConsentRecordResponse(BaseModel):
    """Response for recording consent"""
    status: str
    message: str
    data: Optional[ConsentData] = None


class ConsentListResponse(BaseModel):
    """Response for listing consents"""
    status: str
    message: str
    data: List[ConsentData]


class ConsentBulkResponse(BaseModel):
    """Response for bulk consent (without product_id in data)"""
    status: str
    message: str
    data: List[ConsentBulkData]


class ManageConsentResponse(BaseModel):
    """Response for manage consent"""
    status: str
    message: str
    data: dict = Field(..., description="Update summary")


class ProductConsentStatus(BaseModel):
    """Product consent status for manage consent page"""
    product_id: int
    product_name: Optional[str] = None
    member_id: Optional[int] = None  # member_id if consent exists
    has_consent: bool
    consent_status: str  # "yes" or "no"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ManageConsentPageResponse(BaseModel):
    """Response for manage consent page - shows all products with status"""
    status: str
    message: str
    data: List[ProductConsentStatus]


# OTP-Based Partner Consent Schemas (Product 11)
class PartnerConsentRequestRequest(BaseModel):
    """Request schema for initiating partner consent request (OTP-based)"""
    product_id: int = Field(..., description="Product ID (should be 11 for Child simulator)", gt=0)
    partner_mobile: str = Field(..., description="Partner's mobile number")
    partner_name: Optional[str] = Field(None, description="Partner's name (optional)")
    
    @validator('partner_mobile')
    def validate_partner_mobile(cls, v):
        if not v:
            raise ValueError('partner_mobile is required')
        return validate_indian_mobile(v)
    
    @validator('product_id')
    def validate_product_id(cls, v):
        if not isinstance(v, int) or v <= 0:
            raise ValueError('product_id must be a positive integer')
        if v != 11:
            raise ValueError('partner consent is only for product_id 11 (Child simulator)')
        return v


class PartnerConsentRequestResponse(BaseModel):
    """Response for initiating partner consent request"""
    status: str
    message: str
    data: Optional[dict] = None


class PartnerVerifyOTPRequest(BaseModel):
    """Request schema for partner OTP verification"""
    request_id: str = Field(..., description="Request ID from partner consent request")
    partner_mobile: str = Field(..., description="Partner's mobile number")
    otp: str = Field(..., description="OTP received by partner", min_length=4, max_length=4)
    
    @validator('partner_mobile')
    def validate_partner_mobile(cls, v):
        if not v:
            raise ValueError('partner_mobile is required')
        return validate_indian_mobile(v)
    
    @validator('otp')
    def validate_otp(cls, v):
        if not v.isdigit():
            raise ValueError('OTP must be numeric')
        return v


class PartnerVerifyOTPResponse(BaseModel):
    """Response for partner OTP verification"""
    status: str
    message: str
    data: Optional[dict] = None
class PartnerResendOTPRequest(BaseModel):
    """Request schema for resending OTP"""
    request_id: str = Field(..., description="Request ID from partner consent request")


class PartnerResendOTPResponse(BaseModel):
    """Response for resending OTP"""
    status: str
    message: str
    data: Optional[dict] = None


class PartnerCancelRequestRequest(BaseModel):
    """Request schema for cancelling partner consent request"""
    request_id: str = Field(..., description="Request ID from partner consent request")


class PartnerCancelRequestResponse(BaseModel):
    """Response for cancelling partner consent request"""
    status: str
    message: str
    data: Optional[dict] = None


class PartnerRevokeConsentRequest(BaseModel):
    """Request schema for partner revoking consent"""
    partner_mobile: str = Field(..., description="Partner's mobile number")
    otp: str = Field(..., description="OTP for verification (required for security)")
    
    @validator('partner_mobile')
    def validate_partner_mobile(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError('Partner mobile is required')
        return validate_indian_mobile(v.strip())
    
    @validator('otp')
    def validate_otp(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError('OTP is required for revoking consent')
        return v.strip()


class PartnerRevokeConsentResponse(BaseModel):
    """Response for partner revoking consent"""
    status: str
    message: str
    data: Optional[dict] = None


class PartnerConsentStatusResponse(BaseModel):
    """Response for getting partner consent status"""
    status: str
    message: str
    data: Optional[dict] = None

