"""
Models for user-uploaded external lab reports.
"""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, UniqueConstraint
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class UploadedReport(Base):
    __tablename__ = "uploaded_reports"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    member_id = Column(Integer, nullable=True, index=True)

    file_name = Column(String(300), nullable=False)
    content_type = Column(String(100), nullable=True)
    file_path = Column(String(700), nullable=False)  # server-local path or object key
    file_hash = Column(String(64), nullable=True, index=True)  # sha256 hex; used to dedupe retries
    lab_name = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "member_id",
            "file_hash",
            name="uq_uploaded_reports_user_member_hash",
        ),
    )


class UploadedLabResult(Base):
    """
    One row per extracted parameter line from an uploaded report.
    Stored separately from ThyrocareLabResult but shaped similarly for UI re-use.
    """
    __tablename__ = "uploaded_lab_results"

    id = Column(Integer, primary_key=True, index=True)
    uploaded_report_id = Column(Integer, ForeignKey("uploaded_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    member_id = Column(Integer, nullable=True, index=True)

    test_code = Column(String(100), nullable=True)
    description = Column(String(500), nullable=True)
    test_value = Column(String(100), nullable=True)
    normal_val = Column(String(200), nullable=True)
    units = Column(String(100), nullable=True)
    indicator = Column(String(50), nullable=True)          # HIGH/LOW/NORMAL
    group_name = Column(String(200), nullable=True)        # section/panel name
    organ = Column(String(200), nullable=True)             # mapped organ
    category = Column(String(200), nullable=True)          # alias of organ for UI compatibility
    raw_text = Column(Text, nullable=True)                 # debugging/traceability

    sample_date = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(50), nullable=False, default="uploaded")
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "uploaded_report_id",
            "test_code",
            "description",
            name="uq_uploaded_lab_results_report_code_desc",
        ),
    )

