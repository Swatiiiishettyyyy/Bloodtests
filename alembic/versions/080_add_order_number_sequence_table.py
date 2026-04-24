"""Add order_number_sequence table and advance counter from existing orders.

Revision ID: 080_add_order_number_sequence_table
Revises: 079_add_thyrocare_listing_price
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "080_add_order_number_sequence_table"
down_revision: Union[str, None] = "079_add_thyrocare_listing_price"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORDER_NUMBER_BASE = 2627001000


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "order_number_sequence" not in tables:
        op.create_table(
            "order_number_sequence",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        )
        tables.add("order_number_sequence")

    # Determine current max id in the sequence table
    current_max_id = conn.execute(sa.text("SELECT COALESCE(MAX(id), 0) FROM order_number_sequence")).scalar() or 0

    # If orders table doesn't exist, nothing to backfill from
    if "orders" not in tables:
        return

    # Pull order_number values and compute the max sequence implied by existing numeric order numbers.
    # Ignore non-numeric order numbers (e.g., legacy formats like 'ORD000...').
    max_seq_from_orders = 0
    try:
        rows = conn.execute(sa.text("SELECT order_number FROM orders")).fetchall()
        for (order_number,) in rows:
            if order_number is None:
                continue
            s = str(order_number).strip()
            if not s.isdigit():
                continue
            try:
                n = int(s)
            except Exception:
                continue
            if n > ORDER_NUMBER_BASE:
                seq = n - ORDER_NUMBER_BASE
                if seq > max_seq_from_orders:
                    max_seq_from_orders = seq
    except Exception:
        # Best-effort backfill; table creation is the critical part.
        return

    # Advance sequence so future generated numbers do not collide.
    # Per requirement: insert a row with id=max(seq) if current max id is lower.
    if max_seq_from_orders > int(current_max_id):
        conn.execute(
            sa.text("INSERT INTO order_number_sequence (id) VALUES (:id)").bindparams(id=int(max_seq_from_orders))
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "order_number_sequence" in tables:
        op.drop_table("order_number_sequence")

