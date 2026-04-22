"""add utm_tracking table

Revision ID: 068_add_utm_tracking_table
Revises: 067_uq_thyrocare_patient_tracking_order_patient
Create Date: 2026-04-14

Tags: utm, analytics"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "068_add_utm_tracking_table"
down_revision: Union[str, None] = "067_uq_thyrocare_patient_tracking_order_patient"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()

    if "utm_tracking" in tables:
        return

    op.create_table(
        "utm_tracking",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(length=100), nullable=True),
        sa.Column("utm_source", sa.String(length=255), nullable=True),
        sa.Column("utm_medium", sa.String(length=255), nullable=True),
        sa.Column("utm_campaign", sa.String(length=255), nullable=True),
        sa.Column("utm_term", sa.String(length=255), nullable=True),
        sa.Column("utm_content", sa.String(length=255), nullable=True),
        sa.Column("landing_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_utm_tracking_user_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_utm_tracking_id"), "utm_tracking", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_utm_tracking_fingerprint"),
        "utm_tracking",
        ["fingerprint"],
        unique=False,
    )
    op.create_index(
        op.f("ix_utm_tracking_user_id"),
        "utm_tracking",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()

    if "utm_tracking" not in tables:
        return

    op.drop_index(op.f("ix_utm_tracking_user_id"), table_name="utm_tracking")
    op.drop_index(op.f("ix_utm_tracking_fingerprint"), table_name="utm_tracking")
    op.drop_index(op.f("ix_utm_tracking_id"), table_name="utm_tracking")
    op.drop_table("utm_tracking")
