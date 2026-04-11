"""
Thyrocare lab result model.
Stores parsed test results extracted from the XML report URL.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class ThyrocareLabResult(Base):
    """One row per test result per patient per order."""
    __tablename__ = "thyrocare_lab_results"

    id = Column(Integer, primary_key=True, index=True)

    # Order / patient identifiers
    thyrocare_order_id = Column(String(50), nullable=False, index=True)   # e.g. VL06D615
    patient_id = Column(String(50), nullable=False, index=True)           # <LEADID> e.g. SP84255997
    order_no = Column(String(100), nullable=True)                         # <LAB_CODE> e.g. 2502096324/IT001

    # Test result fields
    test_code = Column(String(100), nullable=True)                        # <TEST_CODE>
    description = Column(String(500), nullable=True)                      # <Description>
    test_value = Column(String(100), nullable=True)                       # <TEST_VALUE>
    normal_val = Column(String(200), nullable=True)                       # <NORMAL_VAL>
    units = Column(String(100), nullable=True)                            # <UNITS>
    indicator = Column(String(50), nullable=True)                         # <INDICATOR> WHITE/HIGH/LOW
    report_group = Column(String(200), nullable=True)                     # <REPORT_GROUP_ID>
    sample_date = Column(DateTime(timezone=True), nullable=True)          # <SDATE>

    # Metadata
    source = Column(String(50), nullable=False, default="nucleotide")
    category = Column(String(200), nullable=True)                         # to be defined later

    # Internal mapping
    member_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
