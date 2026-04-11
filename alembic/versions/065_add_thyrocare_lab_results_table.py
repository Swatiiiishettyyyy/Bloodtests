"""Add thyrocare_lab_results table

Revision ID: 065_add_thyrocare_lab_results_table
Revises: 064_add_thyrocare_webhook_tables
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa

revision = '065_add_thyrocare_lab_results_table'
down_revision = '064_add_thyrocare_webhook_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'thyrocare_lab_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thyrocare_order_id', sa.String(50), nullable=False),
        sa.Column('patient_id', sa.String(50), nullable=False),
        sa.Column('order_no', sa.String(100), nullable=True),
        sa.Column('test_code', sa.String(100), nullable=True),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('test_value', sa.String(100), nullable=True),
        sa.Column('normal_val', sa.String(200), nullable=True),
        sa.Column('units', sa.String(100), nullable=True),
        sa.Column('indicator', sa.String(50), nullable=True),
        sa.Column('report_group', sa.String(200), nullable=True),
        sa.Column('sample_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source', sa.String(50), nullable=False, server_default='nucleotide'),
        sa.Column('category', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_thyrocare_lab_results_thyrocare_order_id', 'thyrocare_lab_results', ['thyrocare_order_id'])
    op.create_index('ix_thyrocare_lab_results_patient_id', 'thyrocare_lab_results', ['patient_id'])


def downgrade():
    op.drop_table('thyrocare_lab_results')
