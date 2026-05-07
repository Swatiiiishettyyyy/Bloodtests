"""Add thyrocare_request_payload and thyrocare_response_body to order_items

Revision ID: 084_add_thyrocare_booking_payload_response
Revises: 083_add_test_name_patient_name_to_thyrocare_lab_results
Create Date: 2026-05-07
"""

from alembic import op
import sqlalchemy as sa

revision = "084_add_thyrocare_booking_payload_response"
down_revision = "083_add_test_name_patient_name_to_thyrocare_lab_results"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("order_items")]

    if "thyrocare_request_payload" not in cols:
        op.add_column("order_items", sa.Column("thyrocare_request_payload", sa.JSON, nullable=True))

    if "thyrocare_response_body" not in cols:
        op.add_column("order_items", sa.Column("thyrocare_response_body", sa.Text, nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("order_items")]

    if "thyrocare_response_body" in cols:
        op.drop_column("order_items", "thyrocare_response_body")

    if "thyrocare_request_payload" in cols:
        op.drop_column("order_items", "thyrocare_request_payload")
