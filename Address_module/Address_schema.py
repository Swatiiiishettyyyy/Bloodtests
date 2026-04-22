from typing import List, Optional

from pydantic import BaseModel, Field, validator


class ThyrocareAddressRequest(BaseModel):
    """
    Same shape as AddressRequest for Thyrocare flows: no serviceable-location or pincode-table checks.
    Postal code is not restricted to 6 digits (stored up to DB column length).
    """
    address_id: int = Field(..., description="Address ID (use 0 for new address)", ge=0)
    postal_code: str = Field(..., description="Postal code (pincode)", min_length=1, max_length=20)
    address_label: str = Field(..., description="Address label (e.g., 'Home', 'Office')", min_length=1, max_length=50)
    street_address: str = Field(..., description="Street address", min_length=1, max_length=255)
    landmark: Optional[str] = Field(None, description="Landmark (optional)", max_length=255)
    locality: str = Field(..., description="Locality", max_length=150)
    city: str = Field(..., description="City", max_length=100)
    state: str = Field(..., description="State", max_length=100)
    country: str = Field(..., description="Country (defaults to India)", max_length=100)
    save_for_future: bool = Field(..., description="Save address for future use")

    @validator("postal_code")
    def normalize_postal_code(cls, v):
        return (v or "").strip()


class AddressRequest(BaseModel):
    address_id: int = Field(..., description="Address ID (use 0 for new address)", ge=0)
    postal_code: str = Field(..., description="Postal code (pincode) - 6 digits", min_length=6, max_length=6)
    # Removed: first_name, last_name, email, mobile (as per requirements)
    address_label: str = Field(..., description="Address label (e.g., 'Home', 'Office')", min_length=1, max_length=50)
    street_address: str = Field(..., description="Street address", min_length=1, max_length=255)
    landmark: Optional[str] = Field(None, description="Landmark (optional)", max_length=255)
    locality: str = Field(..., description="Locality", max_length=150)
    city: str = Field(..., description="City", max_length=100)
    state: str = Field(..., description="State", max_length=100)
    country: str = Field(..., description="Country (defaults to India)", max_length=100)
    save_for_future: bool = Field(..., description="Save address for future use")
    
    @validator('postal_code')
    def validate_postal_code(cls, v):
        # Clean and validate pincode
        v = v.strip().replace(" ", "")
        if len(v) != 6 or not v.isdigit():
            raise ValueError('Postal code must be 6 digits')
        return v


class EditAddressRequest(BaseModel):
    """Request schema for editing address - all fields optional for autofill"""
    postal_code: Optional[str] = Field(None, description="Postal code (pincode) - 6 digits", min_length=6, max_length=6)
    address_label: Optional[str] = Field(None, description="Address label (e.g., 'Home', 'Office')", min_length=1, max_length=50)
    street_address: Optional[str] = Field(None, description="Street address", min_length=1, max_length=255)
    landmark: Optional[str] = Field(None, description="Landmark", max_length=255)
    locality: Optional[str] = Field(None, description="Locality", max_length=150)
    city: Optional[str] = Field(None, description="City", max_length=100)
    state: Optional[str] = Field(None, description="State", max_length=100)
    country: Optional[str] = Field(None, description="Country (defaults to India)", max_length=100)
    save_for_future: Optional[bool] = Field(None, description="Save address for future use")
    
    @validator('postal_code')
    def validate_postal_code(cls, v):
        if v is not None:
            # Clean and validate pincode
            v = v.strip().replace(" ", "")
            if len(v) != 6 or not v.isdigit():
                raise ValueError('Postal code must be 6 digits')
        return v


class AddressData(BaseModel):
    address_id: int
    user_id: int
    # Removed: first_name, last_name, email, mobile
    address_label: str
    street_address: str
    landmark: Optional[str] = None
    locality: Optional[str] = None
    city: str
    state: str
    postal_code: str
    country: str
    save_for_future: Optional[bool] = None


class AddressResponse(BaseModel):
    status: str
    message: str
    data: AddressData


class AddressListResponse(BaseModel):
    status: str
    message: str
    data: List[AddressData]