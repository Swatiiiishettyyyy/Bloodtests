"""add partner consent OTP flow fields

Revision ID: 038_add_partner_consent_otp_fields
Revises: d30672f6c4e3
Create Date: 2025-01-17

Adds OTP-based partner consent flow fields to partner_consents table:
- Request state machine fields (request_status, request_id)
- OTP tracking fields (otp_expires_at, otp_sent_at)
- Request expiration fields (request_expires_at, last_request_created_at)
- Rate limiting fields (failed_attempts, resend_count, total_attempts)
- Revocation tracking (revoked_at)
- Updates default values for partner_consent and consent_source
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "038_add_partner_consent_otp_fields"
down_revision = "d30672f6c4e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()
    
    if 'partner_consents' not in tables:
        return

    existing_columns = {col['name'] for col in inspector.get_columns('partner_consents')}

    def add_col(col_name, col_def):
        if col_name not in existing_columns:
            op.add_column('partner_consents', col_def)

    add_col('request_status', sa.Column('request_status', sa.String(20), nullable=False, server_default='PENDING_REQUEST'))
    add_col('request_id', sa.Column('request_id', sa.String(50), nullable=True))
    add_col('otp_expires_at', sa.Column('otp_expires_at', sa.DateTime(timezone=True), nullable=True))
    add_col('otp_sent_at', sa.Column('otp_sent_at', sa.DateTime(timezone=True), nullable=True))
    add_col('request_expires_at', sa.Column('request_expires_at', sa.DateTime(timezone=True), nullable=True))
    add_col('last_request_created_at', sa.Column('last_request_created_at', sa.DateTime(timezone=True), nullable=True))
    add_col('failed_attempts', sa.Column('failed_attempts', sa.Integer(), nullable=False, server_default='0'))
    add_col('resend_count', sa.Column('resend_count', sa.Integer(), nullable=False, server_default='0'))
    add_col('total_attempts', sa.Column('total_attempts', sa.Integer(), nullable=False, server_default='1'))
    add_col('revoked_at', sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))

    # Create indexes only if they don't exist
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('partner_consents')}
    if 'ix_partner_consents_request_status' not in existing_indexes:
        op.create_index('ix_partner_consents_request_status', 'partner_consents', ['request_status'], unique=False)
    if 'ix_partner_consents_request_id' not in existing_indexes:
        op.create_index('ix_partner_consents_request_id', 'partner_consents', ['request_id'], unique=True)

    try:
        op.execute(text("""
            UPDATE partner_consents 
            SET request_status = CASE 
                WHEN final_status = 'yes' THEN 'CONSENT_GIVEN'
                WHEN partner_consent = 'no' THEN 'DECLINED'
                ELSE 'PENDING_REQUEST'
            END
        """))
    except Exception as e:
        print(f"Warning: Could not update existing request_status: {e}")

    try:
        dialect_name = connection.dialect.name
        if dialect_name == 'mysql':
            op.execute(text("ALTER TABLE partner_consents MODIFY COLUMN partner_consent VARCHAR(10) NOT NULL DEFAULT 'pending'"))
            op.execute(text("ALTER TABLE partner_consents MODIFY COLUMN consent_source VARCHAR(20) NOT NULL DEFAULT 'partner_otp'"))
        else:
            op.alter_column('partner_consents', 'partner_consent', server_default='pending')
            op.alter_column('partner_consents', 'consent_source', server_default='partner_otp')
    except Exception as e:
        print(f"Warning: Could not update column defaults: {e}")


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()
    
    if 'partner_consents' in tables:
        # Drop indexes first
        try:
            op.drop_index('ix_partner_consents_request_id', table_name='partner_consents')
            op.drop_index('ix_partner_consents_request_status', table_name='partner_consents')
        except Exception:
            pass
        
        # Drop columns
        try:
            op.drop_column('partner_consents', 'revoked_at')
            op.drop_column('partner_consents', 'total_attempts')
            op.drop_column('partner_consents', 'resend_count')
            op.drop_column('partner_consents', 'failed_attempts')
            op.drop_column('partner_consents', 'last_request_created_at')
            op.drop_column('partner_consents', 'request_expires_at')
            op.drop_column('partner_consents', 'otp_sent_at')
            op.drop_column('partner_consents', 'otp_expires_at')
            op.drop_column('partner_consents', 'request_id')
            op.drop_column('partner_consents', 'request_status')
        except Exception:
            pass

