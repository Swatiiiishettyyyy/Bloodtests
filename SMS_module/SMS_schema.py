from pydantic import BaseModel


class SMSSendResponse(BaseModel):
    status: str = "success"
    message: str


class WelcomeSMSRequest(BaseModel):
    country_code: str
    mobile: str
    name: str


class ReportReadySMSRequest(BaseModel):
    country_code: str
    mobile: str
    url: str


class OrderConfirmationSMSRequest(BaseModel):
    country_code: str
    mobile: str
    order_id: str


class PhleboBloodSMSRequest(BaseModel):
    country_code: str
    mobile: str
    order_id: str


class PhleboGeneticSMSRequest(BaseModel):
    country_code: str
    mobile: str
    order_id: str


class PostOrderSurveySMSRequest(BaseModel):
    country_code: str
    mobile: str
    survey_url: str
