"""Add test_name and patient_name to thyrocare_lab_results

Revision ID: 083_add_test_name_patient_name_to_thyrocare_lab_results
Revises: 082_add_uploaded_lab_results_table
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa

revision = "083_add_test_name_patient_name_to_thyrocare_lab_results"
down_revision = "082_add_uploaded_lab_results_table"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("thyrocare_lab_results")]

    if "test_name" not in cols:
        op.add_column("thyrocare_lab_results", sa.Column("test_name", sa.String(500), nullable=True))

    if "patient_name" not in cols:
        op.add_column("thyrocare_lab_results", sa.Column("patient_name", sa.String(200), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("thyrocare_lab_results")]

    if "patient_name" in cols:
        op.drop_column("thyrocare_lab_results", "patient_name")

    if "test_name" in cols:
        op.drop_column("thyrocare_lab_results", "test_name")
