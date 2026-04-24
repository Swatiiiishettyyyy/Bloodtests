"""Add uploaded_reports table

Revision ID: 081_add_uploaded_reports_table
Revises: 080_add_order_number_sequence_table
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "081_add_uploaded_reports_table"
down_revision = "080_add_order_number_sequence_table"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uploaded_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("file_path", sa.String(700), nullable=False),
        sa.Column("lab_name", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_uploaded_reports_user_id", "uploaded_reports", ["user_id"])
    op.create_index("ix_uploaded_reports_member_id", "uploaded_reports", ["member_id"])


def downgrade():
    op.drop_table("uploaded_reports")

