"""
Thyrocare webhook tracking models.
Stores order status updates received from Thyrocare webhooks.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
from Login_module.Utils.datetime_utils import now_ist


class ThyrocareOrderTracking(Base):
    """
    One row per Thyrocare order — updated on each webhook received.
    Tracks the latest known state of a Thyrocare order.
    """
    __tablename__ = "thyrocare_order_tracking"

    id = Column(Integer, primary_key=True, index=True)
    thyrocare_order_id = Column(String(50), unique=True, nullable=False, index=True)

    # Link back to our internal order (nullable — may not always match)
    our_order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)

    # Latest status from webhook
    current_order_status = Column(String(100), nullable=True)         # e.g. "YET TO ASSIGN"
    current_status_description = Column(String(100), nullable=True)   # e.g. "SAMPLE_COLLECTED"

    # Phlebo info (updated when assigned)
    phlebo_name = Column(String(200), nullable=True)
    phlebo_contact = Column(String(50), nullable=True)

    # Appointment info
    appointment_date = Column(DateTime(timezone=True), nullable=True)

    last_webhook_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)

    # Internal mapping — set at booking time
    user_id = Column(Integer, nullable=True, index=True)
    member_ids = Column(JSON, nullable=True)        # list of member IDs booked under this order
    order_item_ids = Column(JSON, nullable=True)    # list of order_item IDs for this Thyrocare booking
    thyrocare_product_id = Column(Integer, nullable=True, index=True)  # our internal ThyrocareProduct.id
    ref_order_no = Column(String(100), nullable=True, index=True)  # e.g. "ORD-2024-00123_1"

    # Relationships
    our_order = relationship("Order", foreign_keys=[our_order_id])
    patients = relationship("ThyrocarePatientTracking", back_populates="order_tracking", cascade="all, delete-orphan")
    status_history = relationship("ThyrocareOrderStatusHistory", back_populates="order_tracking", cascade="all, delete-orphan")


class ThyrocarePatientTracking(Base):
    """
    One row per patient (SP* id) per Thyrocare order.
    Updated on each webhook that includes patient data.
    """
    __tablename__ = "thyrocare_patient_tracking"
    __table_args__ = (
        UniqueConstraint(
            "thyrocare_order_id",
            "patient_id",
            name="uq_thyrocare_patient_order_patient",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    thyrocare_order_id = Column(String(50), nullable=False, index=True)
    order_tracking_id = Column(Integer, ForeignKey("thyrocare_order_tracking.id", ondelete="CASCADE"), nullable=False, index=True)

    patient_id = Column(String(50), nullable=False, index=True)   # e.g. "SP71210836"
    patient_name = Column(String(200), nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(String(20), nullable=True)

    is_report_available = Column(Boolean, nullable=True, default=False)
    report_url = Column(Text, nullable=True)
    report_pdf_s3_url = Column(Text, nullable=True)
    report_pdf_s3_key = Column(Text, nullable=True)
    report_timestamp = Column(DateTime(timezone=True), nullable=True)

    current_status = Column(String(100), nullable=True)

    # Internal mapping
    member_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)

    order_tracking = relationship("ThyrocareOrderTracking", back_populates="patients")


class ThyrocareOrderStatusHistory(Base):
    """
    One row per distinct (order_status + order_status_description) on an order tracking row.
    Repeat webhooks refresh raw_payload, thyrocare_timestamp, b2c_patient_id, and received_at.
    """
    __tablename__ = "thyrocare_order_status_history"

    id = Column(Integer, primary_key=True, index=True)
    thyrocare_order_id = Column(String(50), nullable=False, index=True)
    order_tracking_id = Column(Integer, ForeignKey("thyrocare_order_tracking.id", ondelete="CASCADE"), nullable=False, index=True)

    order_status = Column(String(100), nullable=True)
    order_status_description = Column(String(100), nullable=True)
    thyrocare_timestamp = Column(String(50), nullable=True)   # raw "dd-MM-yyyy HH:mm" from webhook
    b2c_patient_id = Column(String(50), nullable=True)

    raw_payload = Column(JSON, nullable=True)   # full webhook payload stored for debugging
    received_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    order_tracking = relationship("ThyrocareOrderTracking", back_populates="status_history")
