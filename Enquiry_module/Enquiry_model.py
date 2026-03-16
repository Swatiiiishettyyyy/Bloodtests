"""
Enquiry / test request model - stores requests for tests (name, contact, email, etc.).
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, func

from database import Base
from Login_module.Utils.datetime_utils import now_ist


class EnquiryRequest(Base):
    """
    Stores enquiry requests from the form: name, contact number, email,
    number of tests required, optional organization and notes.
    """

    __tablename__ = "enquiry_requests"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    contact_number = Column(String(50), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    number_of_tests = Column(Integer, nullable=False)
    organization = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False, index=True)
