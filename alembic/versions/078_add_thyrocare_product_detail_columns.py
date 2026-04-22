"""Add thyrocare_price and product detail text columns to thyrocare_products

Revision ID: 078_add_thyrocare_product_detail_columns
Revises: 077_add_thyrocare_report_pdf_s3_key
Create Date: 2026-04-20

Columns may already exist on some databases; upgrade is idempotent.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "078_add_thyrocare_product_detail_columns"
down_revision: Union[str, None] = "077_add_thyrocare_report_pdf_s3_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(inspector, table: str, col: str) -> bool:
    return col in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "thyrocare_products" not in inspector.get_table_names():
        return
    t = "thyrocare_products"
    if not _col_exists(inspector, t, "thyrocare_price"):
        op.add_column(t, sa.Column("thyrocare_price", sa.Float(), nullable=True))
    if not _col_exists(inspector, t, "what_this_test_checks"):
        op.add_column(t, sa.Column("what_this_test_checks", sa.Text(), nullable=True))
    if not _col_exists(inspector, t, "who_should_take_this_test"):
        op.add_column(t, sa.Column("who_should_take_this_test", sa.Text(), nullable=True))
    if not _col_exists(inspector, t, "why_doctors_recommend"):
        op.add_column(t, sa.Column("why_doctors_recommend", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "thyrocare_products" not in inspector.get_table_names():
        return
    t = "thyrocare_products"
    if _col_exists(inspector, t, "why_doctors_recommend"):
        op.drop_column(t, "why_doctors_recommend")
    if _col_exists(inspector, t, "who_should_take_this_test"):
        op.drop_column(t, "who_should_take_this_test")
    if _col_exists(inspector, t, "what_this_test_checks"):
        op.drop_column(t, "what_this_test_checks")
    if _col_exists(inspector, t, "thyrocare_price"):
        op.drop_column(t, "thyrocare_price")
