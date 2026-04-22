"""Add report_pdf_s3_url to thyrocare_patient_tracking

Revision ID: 076_add_thyrocare_report_pdf_s3_url
Revises: 075_add_ref_order_tracking_columns
Create Date: 2026-04-18

Stores a stable URL after the report PDF is copied from Thyrocare into our reports bucket.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "076_add_thyrocare_report_pdf_s3_url"
down_revision: Union[str, None] = "075_add_ref_order_tracking_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "thyrocare_patient_tracking",
        sa.Column("report_pdf_s3_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thyrocare_patient_tracking", "report_pdf_s3_url")
