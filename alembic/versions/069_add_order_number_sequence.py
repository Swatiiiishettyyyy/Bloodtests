"""Bridge revision: 069_add_order_number_sequence

Some environments may already have alembic_version set to this revision.
This migration is intentionally a no-op, keeping the revision graph consistent.

Revision ID: 069_add_order_number_sequence
Revises: 068_add_utm_tracking_table
Create Date: 2026-04-15
"""

from typing import Sequence, Union

from alembic import op


revision: str = "069_add_order_number_sequence"
down_revision: Union[str, None] = "068_add_utm_tracking_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op bridge revision
    pass


def downgrade() -> None:
    # No-op bridge revision
    pass

