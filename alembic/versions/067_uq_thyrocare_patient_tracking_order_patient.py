"""Unique (thyrocare_order_id, patient_id) on patient tracking; normalize IDs

Revision ID: 067_uq_thyrocare_patient_tracking_order_patient
Revises: 066_add_member_user_to_thyrocare_tables
Create Date: 2026-04-12
"""
from alembic import op
import sqlalchemy as sa


revision = "067_uq_thyrocare_patient_tracking_order_patient"
down_revision = "066_add_member_user_to_thyrocare_tables"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "thyrocare_patient_tracking" not in insp.get_table_names():
        return

    # Normalize patient_id so SP* keys are case-insensitive for uniqueness
    op.execute(
        sa.text(
            "UPDATE thyrocare_patient_tracking SET patient_id = UPPER(TRIM(patient_id))"
        )
    )

    # Drop duplicate rows (keep highest id per order + patient).
    # MySQL forbids DELETE ... WHERE id IN (SELECT ... same table); use a derived table.
    op.execute(
        sa.text(
            """
            DELETE FROM thyrocare_patient_tracking
            WHERE id NOT IN (
                SELECT max_id FROM (
                    SELECT MAX(id) AS max_id FROM thyrocare_patient_tracking
                    GROUP BY thyrocare_order_id, patient_id
                ) AS _keep_patient_ids
            )
            """
        )
    )

    op.create_unique_constraint(
        "uq_thyrocare_patient_order_patient",
        "thyrocare_patient_tracking",
        ["thyrocare_order_id", "patient_id"],
    )


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "thyrocare_patient_tracking" not in insp.get_table_names():
        return
    op.drop_constraint(
        "uq_thyrocare_patient_order_patient",
        "thyrocare_patient_tracking",
        type_="unique",
    )
