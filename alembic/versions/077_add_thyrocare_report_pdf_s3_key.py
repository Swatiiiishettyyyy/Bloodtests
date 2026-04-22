"""Add report_pdf_s3_key for private-bucket presigned downloads

Revision ID: 077_add_thyrocare_report_pdf_s3_key
Revises: 076_add_thyrocare_report_pdf_s3_url
Create Date: 2026-04-18

Object key is stored so GET /thyrocare/reports/.../download can issue a fresh presigned URL.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "077_add_thyrocare_report_pdf_s3_key"
down_revision: Union[str, None] = "076_add_thyrocare_report_pdf_s3_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "thyrocare_patient_tracking",
        sa.Column("report_pdf_s3_key", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thyrocare_patient_tracking", "report_pdf_s3_key")
