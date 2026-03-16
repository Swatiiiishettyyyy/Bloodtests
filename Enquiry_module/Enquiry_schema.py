"""
Pydantic schemas for enquiry / test request form.
"""
from pydantic import BaseModel, Field, EmailStr, validator
from typing import Optional
from Login_module.Utils.phone_validation import validate_indian_mobile


class EnquiryRequestCreate(BaseModel):
    """Request body for submitting an enquiry. Preferred key order: name, organization, contact_number, email, number_of_tests, notes."""

    name: str = Field(..., min_length=1, max_length=255, description="Full name")
    organization: Optional[str] = Field(None, max_length=255, description="Organization (optional)")
    contact_number: str = Field(..., min_length=1, max_length=50, description="Contact number")
    email: EmailStr = Field(..., description="Email address")
    number_of_tests: int = Field(..., ge=1, description="Number of tests required")
    notes: Optional[str] = Field(None, description="Notes (optional)")

    @validator("contact_number")
    def validate_contact_number(cls, v: str) -> str:
        # Reuse Indian mobile validation so enquiry contact numbers are valid 10-digit mobiles
        return validate_indian_mobile(v)

    class Config:
        json_schema_extra = {
            "example": {
                "name": "John Doe",
                "organization": "Acme Labs",
                "contact_number": "+919876543210",
                "email": "john@example.com",
                "number_of_tests": 5,
                "notes": "Need reports by next week",
            }
        }


class EnquiryResponse(BaseModel):
    """Response after submitting an enquiry."""

    status: str = "success"
    message: str = "Request received! Our team will contact you shortly."
    # Data in order: name, organization, contact_number, email, number_of_tests, notes
    name: str = Field(..., description="Full name")
    organization: Optional[str] = Field(None, description="Organization (optional)")
    contact_number: str = Field(..., description="Contact number")
    email: str = Field(..., description="Email address")
    number_of_tests: int = Field(..., description="Number of tests required")
    notes: Optional[str] = Field(None, description="Notes (optional)")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Request received! Our team will contact you shortly.",
                "name": "John Doe",
                "organization": "Acme Labs",
                "contact_number": "+919876543210",
                "email": "john@example.com",
                "number_of_tests": 5,
                "notes": "Need reports by next week",
            }
        }
