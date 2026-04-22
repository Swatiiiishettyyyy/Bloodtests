"""Add order number counter table (sequential ORD##########)

Revision ID: 072_add_order_number_counter
Revises: 071_add_thyrocare_ref_order_counter
Create Date: 2026-04-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "072_add_order_number_counter"
down_revision: Union[str, None] = "071_add_thyrocare_ref_order_counter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_LAST = 0


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()

    if "order_number_counter" not in tables:
        op.create_table(
            "order_number_counter",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("last_value", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    # Seed row if missing. Use quoted identifier for MySQL compatibility (LAST_VALUE()).
    op.execute(
        sa.text(
            """
            INSERT INTO order_number_counter (id, `last_value`)
            SELECT 1, :lv
            WHERE NOT EXISTS (
                SELECT 1 FROM order_number_counter WHERE id = 1
            )
            """
        ).bindparams(lv=_SEED_LAST)
    )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()

    if "order_number_counter" not in tables:
        return

    op.drop_table("order_number_counter")

