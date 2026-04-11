"""Stub migration for 057_add_emi_to_products (file was missing)

Revision ID: 057_add_emi_to_products
Revises: 056_add_coupon_allowed_users
Create Date: 2026-04-09

This is a stub to restore the broken migration chain.
The original migration file was missing from the repository.
"""
from alembic import op
import sqlalchemy as sa

revision = '057_add_emi_to_products'
down_revision = '056_add_coupon_allowed_users'
branch_labels = None
depends_on = None


def upgrade():
    # Stub — original migration was already applied to the database
    pass


def downgrade():
    # Stub — no-op
    pass
