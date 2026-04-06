"""add notifications_enabled to users

Revision ID: 050_add_notifications_enabled
Revises: 049_add_notifications_and_device
Create Date: 2026-02-18

Tags: users, notifications, settings
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "050_add_notifications_enabled"
down_revision: Union[str, None] = "049_add_notifications_and_device"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name='users' AND column_name='notifications_enabled'"
    ))
    if result.scalar() == 0:
        op.add_column(
            "users",
            sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    op.drop_column("users", "notifications_enabled")
