"""Add unique constraint on thyrocare_lab_results (thyrocare_order_id, patient_id, test_code)

Revision ID: 070_uq_thyrocare_lab_results_order_patient_test
Revises: 069_add_thyrocare_ref_order_counter
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "070_uq_thyrocare_lab_results_order_patient_test"
down_revision: Union[str, None] = "069_add_order_number_sequence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "thyrocare_lab_results"
_UQ_NAME = "uq_thyrocare_lab_results_order_patient_test"


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Remove duplicate rows before adding the constraint, keeping the latest id per group.
    # MySQL forbids DELETE ... WHERE id NOT IN (SELECT ... same table); use a derived table.
    connection.execute(
        sa.text(
            f"""
            DELETE FROM {_TABLE}
            WHERE id NOT IN (
                SELECT max_id FROM (
                    SELECT MAX(id) AS max_id
                    FROM {_TABLE}
                    GROUP BY thyrocare_order_id, patient_id, test_code
                ) AS _keep_ids
            )
            """
        )
    )

    existing_uqs = [
        uq["name"]
        for uq in inspector.get_unique_constraints(_TABLE)
        if uq.get("name")
    ]
    if _UQ_NAME not in existing_uqs:
        op.create_unique_constraint(
            _UQ_NAME,
            _TABLE,
            ["thyrocare_order_id", "patient_id", "test_code"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_uqs = [
        uq["name"]
        for uq in inspector.get_unique_constraints(_TABLE)
        if uq.get("name")
    ]
    if _UQ_NAME in existing_uqs:
        op.drop_constraint(_UQ_NAME, _TABLE, type_="unique")
