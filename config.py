import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    # Dev-friendly defaults: when not provided in env/.env, backend can still boot
    # (database.py already has its own SQLite fallback).
    DATABASE_URL: str = ""
    SECRET_KEY: str = "dev_secret_key_change_me"
    ALGORITHM: str = "HS256"
    
    # Token configuration - Dual Token Strategy
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15  # Default: 15 minutes
    ACCESS_TOKEN_EXPIRE_SECONDS: int = 900  # Will be recalculated from minutes if not set in env
    REFRESH_TOKEN_EXPIRE_DAYS_WEB: float = 7.0  # Default: 7 days
    REFRESH_TOKEN_EXPIRE_DAYS_MOBILE: float = 7.0  # Default: 7 days
    
    # Cookie configuration for web
    COOKIE_DOMAIN: str = ""
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    # Separate SameSite setting for refresh token cookie (often same as access cookie)
    REFRESH_COOKIE_SAMESITE: str = "lax"
    
    # CSRF protection
    CSRF_SECRET_KEY: str = ""
    
    # OTP config - can be overridden via .env
    OTP_EXPIRY_SECONDS: int = 120  # 2 minutes
    OTP_MAX_REQUESTS_PER_HOUR: int = 15  # 15 per hour
    OTP_MAX_FAILED_ATTEMPTS: int = 5  # Block after 5 failed attempts
    OTP_BLOCK_DURATION_SECONDS: int = 600  # 10 minutes block
    
    # Session management
    MAX_ACTIVE_SESSIONS: int = 4  # Max 4 active sessions per user
    
    # Refresh token rate limiting - can be overridden via .env
    REFRESH_TOKEN_MAX_ATTEMPTS_PER_SESSION: int = 20  # 20 requests per hour per session
    REFRESH_TOKEN_WINDOW_SECONDS: int = 3600  # 1 hour window
    REFRESH_TOKEN_MAX_FAILED_ATTEMPTS: int = 10  # Block after 10 failures
    
    # OTP verification rate limiting - can be overridden via .env
    VERIFY_OTP_MAX_ATTEMPTS_PER_IP: int = 10  # 10 attempts per hour per IP
    VERIFY_OTP_WINDOW_SECONDS: int = 3600  # 1 hour window
    
    # Maximum session lifetime (absolute expiration - prevents indefinite sessions)
    # Should match refresh token lifetime for consistency
    # Can be overridden via .env: MAX_SESSION_LIFETIME_DAYS=7 (7 days for production)
    MAX_SESSION_LIFETIME_DAYS: float = 7.0  # Default: 7 days

    ENVIRONMENT: str = "development"

    # Twilio Verify API - for SMS verification (separate from custom OTP flow)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_VERIFY_SERVICE_SID: str = ""

    # MSG91 (Flow API) - OTP SMS
    MSG91_FLOW_URL: str = "https://control.msg91.com/api/v5/flow"
    MSG91_AUTH_KEY: str = ""
    MSG91_OTP_TEMPLATE_ID: str = ""

    # MSG91 (Flow API) - transactional messages
    MSG91_ORDER_PLACED_TEMPLATE_ID: str = ""
    MSG91_THYROCARE_ARRIVED_TEMPLATE_ID: str = ""

    # Firebase FCM - path to service account JSON; empty = skip FCM send (notifications still stored in DB)
    FIREBASE_SERVICE_ACCOUNT_PATH: str = ""
    # When True (default), invalid FCM tokens are removed after failed send. When False (e.g. dev/test with dummy token), tokens are kept so notification trigger keeps running for every event.
    REMOVE_INVALID_FCM_TOKENS: bool = True

    # Invoice Generation & Sending
    INVOICE_SERVICE_ACCOUNT_PATH: str = "invoice generation/billing.json"
    INVOICE_SENDER_EMAIL: str = "billing@nucleotide.life"
    INVOICE_COMPANY_NAME: str = "Nucleotide Healthcare Pvt Ltd"
    INVOICE_COMPANY_ADDRESS: str = "Bangalore, Karnataka, India"
    INVOICE_PAN_NUMBER: str = "AADCE5479M"
    INVOICE_SAC_CODE: str = "999312"
    INVOICE_CUSTOMER_CARE_PHONE: str = "+91 9403891587"
    INVOICE_CUSTOMER_CARE_EMAIL: str = "info@nucleotide.life"
    INVOICE_WEBSITE: str = "www.nucleotide.life"
    INVOICE_LOGO_PATH: str = "invoice generation/logo.png"
    # Comma-separated BCC addresses for invoice emails, e.g. "a@x.com,b@x.com"
    INVOICE_BCC_EMAILS: str = ""
    # Public S3/CDN URL for the delivery-boy GIF used in the order confirmation email
    ORDER_CONFIRMATION_GIF_URL: str = ""

    # Thyrocare Configuration
    THYROCARE_BASE_URL: str = "https://api-sandbox.thyrocare.com"
    THYROCARE_USERNAME: str = ""
    THYROCARE_PASSWORD: str = ""
    THYROCARE_PARTNER_ID: str = ""
    THYROCARE_CLIENT_TYPE: str = "All"
    THYROCARE_REQUEST_ID: str = "Pass"
    THYROCARE_ENTITY_TYPE: str = "DSA"
    THYROCARE_PAY_TYPE: str = "POSTPAID"

    # Admin password for PUT /orders/status endpoint — override via ORDER_STATUS_PASSWORD env var
    ORDER_STATUS_PASSWORD: str = "change_me_in_env"

    # Internal tooling: allow resetting order_number_sequence (non-production only).
    ALLOW_ORDER_NUMBER_RESET: bool = False

    model_config = ConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # Ignore extra env vars not defined in Settings
    )

# Create the settings instance
settings = Settings()

# ACCESS_TOKEN_EXPIRE_SECONDS is always derived from ACCESS_TOKEN_EXPIRE_MINUTES.
# Whatever is set in env (locally or in AWS App Runner) is the final value — no cap applied.
settings.ACCESS_TOKEN_EXPIRE_SECONDS = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

# Fallback CSRF_SECRET_KEY if not set (use SECRET_KEY with prefix)
if not settings.CSRF_SECRET_KEY:
    settings.CSRF_SECRET_KEY = f"csrf_{settings.SECRET_KEY}"
