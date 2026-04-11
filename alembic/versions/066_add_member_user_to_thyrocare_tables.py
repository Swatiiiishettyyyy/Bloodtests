"""Add member_id and user_id to thyrocare tracking tables

Revision ID: 066_add_member_user_to_thyrocare_tables
Revises: 065_add_thyrocare_lab_results_table
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa

revision = '066_add_member_user_to_thyrocare_tables'
down_revision = '065_add_thyrocare_lab_results_table'
branch_labels = None
depends_on = None


def _col_exists(inspector, table, col):
    return col in {c['name'] for c in inspector.get_columns(table)}


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # thyrocare_order_tracking
    if 'thyrocare_order_tracking' in inspector.get_table_names():
        if not _col_exists(inspector, 'thyrocare_order_tracking', 'user_id'):
            op.add_column('thyrocare_order_tracking', sa.Column('user_id', sa.Integer(), nullable=True))
            op.create_index('ix_thyrocare_order_tracking_user_id', 'thyrocare_order_tracking', ['user_id'])
        if not _col_exists(inspector, 'thyrocare_order_tracking', 'member_ids'):
            op.add_column('thyrocare_order_tracking', sa.Column('member_ids', sa.JSON(), nullable=True))

    # thyrocare_patient_tracking
    if 'thyrocare_patient_tracking' in inspector.get_table_names():
        if not _col_exists(inspector, 'thyrocare_patient_tracking', 'member_id'):
            op.add_column('thyrocare_patient_tracking', sa.Column('member_id', sa.Integer(), nullable=True))
            op.create_index('ix_thyrocare_patient_tracking_member_id', 'thyrocare_patient_tracking', ['member_id'])
        if not _col_exists(inspector, 'thyrocare_patient_tracking', 'user_id'):
            op.add_column('thyrocare_patient_tracking', sa.Column('user_id', sa.Integer(), nullable=True))
            op.create_index('ix_thyrocare_patient_tracking_user_id', 'thyrocare_patient_tracking', ['user_id'])

    # thyrocare_lab_results
    if 'thyrocare_lab_results' in inspector.get_table_names():
        if not _col_exists(inspector, 'thyrocare_lab_results', 'member_id'):
            op.add_column('thyrocare_lab_results', sa.Column('member_id', sa.Integer(), nullable=True))
            op.create_index('ix_thyrocare_lab_results_member_id', 'thyrocare_lab_results', ['member_id'])
        if not _col_exists(inspector, 'thyrocare_lab_results', 'user_id'):
            op.add_column('thyrocare_lab_results', sa.Column('user_id', sa.Integer(), nullable=True))
            op.create_index('ix_thyrocare_lab_results_user_id', 'thyrocare_lab_results', ['user_id'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    for table, cols in [
        ('thyrocare_lab_results', ['member_id', 'user_id']),
        ('thyrocare_patient_tracking', ['member_id', 'user_id']),
        ('thyrocare_order_tracking', ['user_id', 'member_ids']),
    ]:
        if table in inspector.get_table_names():
            for col in cols:
                if _col_exists(inspector, table, col):
                    try:
                        op.drop_index(f'ix_{table}_{col}', table_name=table)
                    except Exception:
                        pass
                    op.drop_column(table, col)
