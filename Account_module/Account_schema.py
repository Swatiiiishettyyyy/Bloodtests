"""
Pydantic schemas for account feedback (phone change and account deletion reasons).
"""
from pydantic import BaseModel, Field, validator
from typing import Optional
from Login_module.Utils.phone_validation import validate_indian_mobile


class AccountFeedbackRequestBody(BaseModel):
    """
    Request body for account feedback form.

    Frontend form has two sections:
    - Phone number change reason
    - Account deletion reason

    Both fields are optional, but at least one must be provided.
    """

    current_phone: Optional[str] = Field(
        None,
        description="Current phone number shown in the form (optional, for context).",
    )
    new_phone: Optional[str] = Field(
        None,
        description="New phone number to change to (required for phone change requests).",
    )
    phone_change_reason: Optional[str] = Field(
        None,
        description="Reason for requesting phone number change (optional).",
    )
    account_delete_reason: Optional[str] = Field(
        None,
        description="Reason for requesting account deletion (optional).",
    )

    @validator("current_phone")
    def validate_current_phone(cls, v: Optional[str]) -> Optional[str]:
        # Allow empty/None (field is optional), but if provided, enforce valid Indian mobile
        if v is None or v.strip() == "":
            return v
        return validate_indian_mobile(v)

    @validator("new_phone")
    def validate_new_phone(cls, v: Optional[str]) -> Optional[str]:
        # Allow empty/None, but when provided for phone-change, enforce valid Indian mobile
        if v is None or v.strip() == "":
            return v
        return validate_indian_mobile(v)


