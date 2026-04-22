"""Thyrocare refOrderNo counter table (seed 2627001000)

Revision ID: 071_add_thyrocare_ref_order_counter
Revises: 070_uq_thyrocare_lab_results_order_patient_test
Create Date: 2026-04-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "071_add_thyrocare_ref_order_counter"
down_revision: Union[str, None] = "070_uq_thyrocare_lab_results_order_patient_test"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# First issued ref will be SEED_LAST + 1 (2627001001)
_SEED_LAST = 2627001000


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()

    if "thyrocare_ref_order_counter" not in tables:
        op.create_table(
            "thyrocare_ref_order_counter",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("last_value", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    # Seed row if missing. Use quoted identifier for MySQL compatibility (LAST_VALUE()).
    op.execute(
        sa.text(
            """
            INSERT INTO thyrocare_ref_order_counter (id, `last_value`)
            SELECT 1, :lv
            WHERE NOT EXISTS (
                SELECT 1 FROM thyrocare_ref_order_counter WHERE id = 1
            )
            """
        ).bindparams(lv=_SEED_LAST)
    )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()

    if "thyrocare_ref_order_counter" not in tables:
        return

    op.drop_table("thyrocare_ref_order_counter")

