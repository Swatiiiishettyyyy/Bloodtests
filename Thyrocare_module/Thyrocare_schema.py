from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date


class ThyrocareTestParameterOut(BaseModel):
    id: int
    name: str
    group_name: Optional[str] = None

    class Config:
        from_attributes = True


class ThyrocareProductOut(BaseModel):
    id: int
    thyrocare_id: str
    name: str
    type: str
    no_of_tests_included: int
    listing_price: float
    selling_price: float
    discount_percentage: float
    beneficiaries_min: int
    beneficiaries_max: int
    is_fasting_required: Optional[bool] = None
    is_home_collectible: Optional[bool] = None
    about: Optional[str] = None
    short_description: Optional[str] = None
    parameters: List[ThyrocareTestParameterOut] = []

    class Config:
        from_attributes = True


class ThyrocareProductUpdate(BaseModel):
    about: Optional[str] = None
    short_description: Optional[str] = None
    is_active: Optional[bool] = None
    selling_price: Optional[float] = None
    listing_price: Optional[float] = None


# Cart schemas for blood tests
class BloodTestCartAdd(BaseModel):
    thyrocare_product_id: int = Field(..., gt=0)
    member_ids: List[int] = Field(..., min_items=1)
    address_id: int = Field(..., gt=0)


class SlotSearchRequest(BaseModel):
    group_id: str = Field(..., description="Cart group_id from blood test cart add")
    date_from: Optional[date] = Field(None, description="Start of date range (defaults to today)")
    date_to: Optional[date] = Field(None, description="End of date range (max 7 days from date_from)")
    appointment_date: Optional[date] = Field(None, description="Single date (YYYY-MM-DD). Use this OR date_from/date_to.")


class AppointmentSetRequest(BaseModel):
    group_id: str
    appointment_date: date
    appointment_start_time: str = Field(..., description="e.g. '09:00'")


class PriceBreakupRequest(BaseModel):
    group_id: str = Field(..., description="Cart group_id from blood test cart add")
    is_report_hard_copy_required: bool = Field(False, description="Whether physical report copy is needed")


class BloodTestOrderCreate(BaseModel):
    group_id: str = Field(..., description="Cart group_id from blood test cart add")
    order_id: Optional[int] = Field(None, description="Internal order ID (from orders table after payment). If not provided, a ref_order_no is auto-generated.")
    pay_type: str = Field("POSTPAID", description="POSTPAID or PREPAID")
    is_report_hard_copy_required: bool = Field(False)
    remarks: Optional[str] = Field(None, description="Additional remarks for the order")
    phlebo_notes: Optional[str] = Field(None, description="Notes for the phlebotomist")
    incentive_passon_value: Optional[str] = Field(None, description="Incentive value to pass on to customer (FLAT amount as string)")
