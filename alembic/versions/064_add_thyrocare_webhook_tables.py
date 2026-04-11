"""Add thyrocare webhook tracking tables

Revision ID: 064_add_thyrocare_webhook_tables
Revises: 063_add_thyrocare_pincodes_table
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa

revision = '064_add_thyrocare_webhook_tables'
down_revision = '063_add_thyrocare_pincodes_table'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'thyrocare_order_tracking',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thyrocare_order_id', sa.String(50), unique=True, nullable=False),
        sa.Column('our_order_id', sa.Integer(), sa.ForeignKey('orders.id', ondelete='SET NULL'), nullable=True),
        sa.Column('current_order_status', sa.String(100), nullable=True),
        sa.Column('current_status_description', sa.String(100), nullable=True),
        sa.Column('phlebo_name', sa.String(200), nullable=True),
        sa.Column('phlebo_contact', sa.String(50), nullable=True),
        sa.Column('appointment_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_webhook_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_thyrocare_order_tracking_thyrocare_order_id', 'thyrocare_order_tracking', ['thyrocare_order_id'])
    op.create_index('ix_thyrocare_order_tracking_our_order_id', 'thyrocare_order_tracking', ['our_order_id'])

    op.create_table(
        'thyrocare_patient_tracking',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thyrocare_order_id', sa.String(50), nullable=False),
        sa.Column('order_tracking_id', sa.Integer(), sa.ForeignKey('thyrocare_order_tracking.id', ondelete='CASCADE'), nullable=False),
        sa.Column('patient_id', sa.String(50), nullable=False),
        sa.Column('patient_name', sa.String(200), nullable=True),
        sa.Column('age', sa.Integer(), nullable=True),
        sa.Column('gender', sa.String(20), nullable=True),
        sa.Column('is_report_available', sa.Boolean(), nullable=True, default=False),
        sa.Column('report_url', sa.Text(), nullable=True),
        sa.Column('report_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_status', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_thyrocare_patient_tracking_thyrocare_order_id', 'thyrocare_patient_tracking', ['thyrocare_order_id'])
    op.create_index('ix_thyrocare_patient_tracking_patient_id', 'thyrocare_patient_tracking', ['patient_id'])
    op.create_index('ix_thyrocare_patient_tracking_order_tracking_id', 'thyrocare_patient_tracking', ['order_tracking_id'])

    op.create_table(
        'thyrocare_order_status_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thyrocare_order_id', sa.String(50), nullable=False),
        sa.Column('order_tracking_id', sa.Integer(), sa.ForeignKey('thyrocare_order_tracking.id', ondelete='CASCADE'), nullable=False),
        sa.Column('order_status', sa.String(100), nullable=True),
        sa.Column('order_status_description', sa.String(100), nullable=True),
        sa.Column('thyrocare_timestamp', sa.String(50), nullable=True),
        sa.Column('b2c_patient_id', sa.String(50), nullable=True),
        sa.Column('raw_payload', sa.JSON(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_thyrocare_order_status_history_thyrocare_order_id', 'thyrocare_order_status_history', ['thyrocare_order_id'])
    op.create_index('ix_thyrocare_order_status_history_order_tracking_id', 'thyrocare_order_status_history', ['order_tracking_id'])


def downgrade():
    op.drop_table('thyrocare_order_status_history')
    op.drop_table('thyrocare_patient_tracking')
    op.drop_table('thyrocare_order_tracking')
