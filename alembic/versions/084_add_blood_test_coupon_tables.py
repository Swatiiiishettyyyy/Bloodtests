"""Add blood_test_coupons, blood_test_cart_coupons, blood_test_coupon_usages,
and blood_test_coupon_allowed_users tables.

Revision ID: 084_add_blood_test_coupon_tables
Revises: 083_add_test_name_patient_name_to_thyrocare_lab_results
Create Date: 2026-05-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "084_add_blood_test_coupon_tables"
down_revision: Union[str, None] = "083_add_test_name_patient_name_to_thyrocare_lab_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    # blood_test_coupons
    if "blood_test_coupons" not in existing:
        op.create_table(
            "blood_test_coupons",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("coupon_code", sa.String(50), unique=True, nullable=False, index=True),
            sa.Column("description", sa.String(500), nullable=True),
            sa.Column(
                "discount_type",
                sa.Enum("percentage", "fixed", name="bloodtestcoupontype"),
                nullable=False,
                server_default="percentage",
            ),
            sa.Column("discount_value", sa.Float(), nullable=False),
            sa.Column("min_order_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("max_discount_amount", sa.Float(), nullable=True),
            sa.Column("max_uses", sa.Integer(), nullable=True),
            sa.Column("max_uses_per_user", sa.Integer(), nullable=True, server_default="1"),
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "status",
                sa.Enum("active", "inactive", "expired", name="bloodtestcouponstatus"),
                nullable=False,
                server_default="active",
                index=True,
            ),
            sa.Column(
                "special_type",
                sa.Enum("standard", "corporate", "family", "founder", name="bloodtestcouponspecialtype"),
                nullable=False,
                server_default="standard",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # blood_test_cart_coupons
    if "blood_test_cart_coupons" not in existing:
        op.create_table(
            "blood_test_cart_coupons",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("user_id", sa.Integer(), nullable=False, index=True),
            sa.Column(
                "coupon_id",
                sa.Integer(),
                sa.ForeignKey("blood_test_coupons.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("coupon_code", sa.String(50), nullable=False, index=True),
            sa.Column("discount_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        )

    # blood_test_coupon_usages
    if "blood_test_coupon_usages" not in existing:
        op.create_table(
            "blood_test_coupon_usages",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column(
                "coupon_id",
                sa.Integer(),
                sa.ForeignKey("blood_test_coupons.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("coupon_code", sa.String(50), nullable=False, index=True),
            sa.Column("user_id", sa.Integer(), nullable=False, index=True),
            sa.Column("order_id", sa.Integer(), nullable=False, index=True),
            sa.Column("order_number", sa.String(50), nullable=False),
            sa.Column("discount_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        )

    # blood_test_coupon_allowed_users
    if "blood_test_coupon_allowed_users" not in existing:
        op.create_table(
            "blood_test_coupon_allowed_users",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column(
                "coupon_id",
                sa.Integer(),
                sa.ForeignKey("blood_test_coupons.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("user_id", sa.Integer(), nullable=True, index=True),
            sa.Column("mobile", sa.String(100), nullable=True, index=True),
            sa.UniqueConstraint("coupon_id", "user_id", name="uq_bt_coupon_user_id"),
            sa.UniqueConstraint("coupon_id", "mobile", name="uq_bt_coupon_mobile"),
            sa.CheckConstraint(
                "user_id IS NOT NULL OR mobile IS NOT NULL",
                name="ck_bt_coupon_allowed_users_not_null",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    for table in [
        "blood_test_coupon_allowed_users",
        "blood_test_coupon_usages",
        "blood_test_cart_coupons",
        "blood_test_coupons",
    ]:
        if table in existing:
            op.drop_table(table)

    # Drop enums (MySQL ignores these but Postgres needs them)
    for enum_name in [
        "bloodtestcoupontype",
        "bloodtestcouponstatus",
        "bloodtestcouponspecialtype",
    ]:
        try:
            sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
        except Exception:
            pass
