"""Add uploaded_lab_results table

Revision ID: 082_add_uploaded_lab_results_table
Revises: 081_add_uploaded_reports_table
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "082_add_uploaded_lab_results_table"
down_revision = "081_add_uploaded_reports_table"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uploaded_lab_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uploaded_report_id", sa.Integer(), sa.ForeignKey("uploaded_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("test_code", sa.String(100), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("test_value", sa.String(100), nullable=True),
        sa.Column("normal_val", sa.String(200), nullable=True),
        sa.Column("units", sa.String(100), nullable=True),
        sa.Column("indicator", sa.String(50), nullable=True),
        sa.Column("group_name", sa.String(200), nullable=True),
        sa.Column("organ", sa.String(200), nullable=True),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("sample_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="uploaded"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("uploaded_report_id", "test_code", "description", name="uq_uploaded_lab_results_report_code_desc"),
    )
    op.create_index("ix_uploaded_lab_results_uploaded_report_id", "uploaded_lab_results", ["uploaded_report_id"])
    op.create_index("ix_uploaded_lab_results_user_id", "uploaded_lab_results", ["user_id"])
    op.create_index("ix_uploaded_lab_results_member_id", "uploaded_lab_results", ["member_id"])


def downgrade():
    op.drop_table("uploaded_lab_results")

