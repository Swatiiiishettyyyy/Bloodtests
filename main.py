"""
Main FastAPI application entry point.
"""
import os
import sys
import time
import logging
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from starlette import status
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.exc import OperationalError

# Load environment variables
load_dotenv()

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True
)

logger = logging.getLogger(__name__)

# Database and migrations
from database import Base, engine
from alembic_runner import run_migrations
from Category_module.bootstrap import seed_default_categories

# Import models to register with SQLAlchemy Base
from Banner_module.Banner_model import Banner
from Consent_module.Consent_model import UserConsent, ConsentProduct, PartnerConsent
from Orders_module.Order_model import Order, OrderItem, OrderSnapshot, OrderStatusHistory
from GeneticTest_module.GeneticTest_model import GeneticTestParticipant
from PhoneChange_module.PhoneChange_model import PhoneChangeRequest, PhoneChangeAuditLog
from Login_module.Token.Refresh_token_model import RefreshToken  # Dual-token strategy
from Newsletter_module.Newsletter_model import NewsletterSubscription
from Tracking_module.Tracking_model import TrackingRecord  # Location & Analytics Tracking
from Account_module.Account_model import AccountFeedbackRequest
from Enquiry_module.Enquiry_model import EnquiryRequest
from Utm_tracking_module.Utm_tracking_model import UtmTracking
from Notification_module.Notification_model import Notification, UserDeviceToken
from Thyrocare_module.Thyrocare_model import ThyrocareProduct, ThyrocareTestParameter
from Thyrocare_module.thyrocare_ref_order_counter_model import ThyrocareRefOrderCounter
from Orders_module.order_number_counter_model import OrderNumberCounter

# Import Google Meet API models to register with SQLAlchemy Base
try:
    from gmeet_api.models import (
        CounsellorToken,
        CounsellorBooking,
        CounsellorActivityLog,
        CounsellorGmeetList
    )
except ImportError:
    logger.warning("Failed to import gmeet_api models. Google Meet tables may not be created.")

# Routers
from Address_module.Address_router import router as address_router
from Banner_module.Banner_router import router as banner_router
from Cart_module.Cart_router import router as cart_router
from Category_module.Category_router import router as category_router
from Consent_module.Consent_router import router as consent_router
from Login_module.OTP.OTP_router import router as otp_router
from Login_module.Twilio.Twilio_router import router as twilio_router
from Login_module.Device.Device_session_router import router as session_router
from Login_module.Utils.audit_query import router as audit_router
from Login_module.Token.Auth_token_router import router as auth_token_router  # Dual-token strategy endpoints
from Member_module.Member_router import router as member_router
from Orders_module.Order_router import router as order_router
from Product_module.Product_router import router as product_router
from PhoneChange_module.PhoneChange_router import router as phone_change_router
from Newsletter_module.Newsletter_router import router as newsletter_router
from Notification_module.Notification_router import router as notification_router
from Tracking_module.Tracking_router import router as tracking_router
from Account_module.Account_router import router as account_router
from Enquiry_module.Enquiry_router import router as enquiry_router
from Utm_tracking_module.Utm_tracking_router import router as utm_tracking_router
from Thyrocare_module.Thyrocare_router import router as thyrocare_router

# Google Meet API router
try:
    from gmeet_api.router import router as gmeet_router
except ImportError:
    logger.warning("Failed to import gmeet_api router. Google Meet endpoints will not be available.")
    gmeet_router = None

# Scheduler
from Login_module.Device.scheduler import start_scheduler, shutdown_scheduler

# Thyrocare Auth Task
from Thyrocare_module.thyrocare_service import start_thyrocare_auth_task


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP requests and responses with status codes, and add token expiration headers."""
    
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        start_time = time.time()
        
        # Log incoming request
        logger.info(f"→ {request.method} {request.url.path} | IP: {client_ip}")
        
        try:
            response = await call_next(request)
            duration = time.time() - start_time
            
            # Get status code - ensure we can access it
            try:
                status_code = response.status_code
            except AttributeError:
                # Fallback if status_code is not directly accessible
                status_code = getattr(response, 'status_code', 200)
            
            # Add token expiration status headers if available in request state
            # This helps clients know when to refresh tokens
            if hasattr(request.state, 'token_expired'):
                if request.state.token_expired:
                    # Token is expired - add warning header
                    response.headers["X-Token-Status"] = "expired"
                    response.headers["X-Token-Warning"] = "Access token has expired. Please refresh using /auth/refresh endpoint."
                elif hasattr(request.state, 'token_valid') and request.state.token_valid:
                    # Token is valid
                    response.headers["X-Token-Status"] = "valid"
                elif hasattr(request.state, 'token_invalid') and request.state.token_invalid:
                    # Token is invalid
                    response.headers["X-Token-Status"] = "invalid"
            
            # Determine status code category and emoji
            if 200 <= status_code < 300:
                status_emoji = "✅"
                status_category = "SUCCESS"
            elif 300 <= status_code < 400:
                status_emoji = "⚠️"
                status_category = "REDIRECT"
            elif 400 <= status_code < 500:
                status_emoji = "❌"
                status_category = "CLIENT_ERROR"
            else:
                status_emoji = "💥"
                status_category = "SERVER_ERROR"
            
            # Log response with detailed status code information - make it more visible
            log_message = (
                f"{status_emoji} {request.method} {request.url.path} | "
                f"Status: {status_code} ({status_category}) | "
                f"Duration: {duration:.3f}s | "
                f"IP: {client_ip}"
            )
            logger.info(log_message)
            
            # Also print to console for better visibility
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {log_message}")
            
            return response
        except Exception as e:
            duration = time.time() - start_time
            error_message = (
                f"💥 {request.method} {request.url.path} | "
                f"Status: 500 (SERVER_ERROR) | "
                f"Error: {str(e)} | "
                f"Duration: {duration:.3f}s | "
                f"IP: {client_ip}"
            )
            logger.error(error_message)
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error_message}")
            raise


def initialize_database():
    """
    Initialize database by running Alembic migrations and seeding default data.
    Handles connection errors gracefully.
    
    Note: For fresh databases, run `python create_all_tables.py` first to create all tables.
    """
    # Run Alembic migrations (handles all schema creation and updates)
    try:
        logger.info("Running database migrations...")
        run_migrations()
        logger.info("Database migrations completed successfully")
    except KeyboardInterrupt:
        # Re-raise keyboard interrupt so the app can shut down gracefully
        logger.warning("Migration interrupted by user")
        raise
    except Exception as e:
        # Catch all exceptions to prevent app crash
        logger.error(f"Error during database migrations: {e}", exc_info=True)
        logger.warning("Migrations will be retried on next startup. Application will continue to start.")
    
    # Seed default categories
    try:
        logger.info("Seeding default categories...")
        seed_default_categories()
        logger.info("Default categories seeded successfully")
    except Exception as e:
        # Catch all exceptions to prevent app crash
        logger.error(f"Error during category seeding: {e}", exc_info=True)
        logger.warning("Category seeding will be retried on next startup. Application will continue to start.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    try:
        logger.info("Starting application...")
        logger.info("Step 1: Initializing database...")
        initialize_database()
        logger.info("Step 2: Database initialization complete")
        logger.info("Step 3: Starting scheduler...")
        start_scheduler()
        logger.info("Step 4: Scheduler started")
        try:
            from Notification_module.firebase_service import init_firebase
            if init_firebase():
                logger.info("FCM (Firebase Cloud Messaging) activated - push notifications enabled")
            else:
                logger.info("FCM not configured - notifications will be stored in DB only (no push)")
        except Exception as e:
            logger.warning("Firebase init skipped or failed: %s", e)
        
        logger.info("Step 5: Initializing Thyrocare authentication...")
        start_thyrocare_auth_task()
        
        logger.info("Application started successfully - all startup tasks completed")
    except KeyboardInterrupt:
        logger.warning("Application startup interrupted by user")
        raise
    except Exception as e:
        logger.error(f"Error during application startup: {e}", exc_info=True)
        logger.warning("Application will continue to start despite startup errors.")
    
    logger.info("Yielding control to FastAPI...")
    yield
    logger.info("FastAPI shutdown initiated...")
    
    try:
        logger.info("Shutting down application...")
        shutdown_scheduler()
        logger.info("Application shutdown complete")
    except Exception as e:
        logger.error(f"Error during application shutdown: {e}", exc_info=True)


# Initialize FastAPI app
app = FastAPI(
    title="Nucleotide API",
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={
        "persistAuthorization": False,  # Clear access token on page refresh
        "displayRequestDuration": True,
    }
)

# Configure OpenAPI to include CSRF token header support
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    from fastapi.openapi.utils import get_openapi
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Ensure components section exists
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    
    # Initialize securitySchemes if it doesn't exist
    if "securitySchemes" not in openapi_schema["components"]:
        openapi_schema["components"]["securitySchemes"] = {}
    
    # Remove any auto-generated security schemes from FastAPI to avoid duplicates
    # FastAPI may auto-generate schemes from HTTPBearer dependencies, but we want only one BearerAuth
    existing_schemes = openapi_schema["components"]["securitySchemes"].copy()
    
    # Remove any existing BearerAuth or HTTPBearer schemes (FastAPI might create them)
    schemes_to_remove = []
    for scheme_name in existing_schemes.keys():
        scheme = existing_schemes[scheme_name]
        # Check if it's a bearer token scheme
        if scheme.get("type") == "http" and scheme.get("scheme") == "bearer":
            schemes_to_remove.append(scheme_name)
    
    # Remove duplicate bearer schemes
    for scheme_name in schemes_to_remove:
        if scheme_name != "BearerAuth":  # Keep our BearerAuth, remove others
            del openapi_schema["components"]["securitySchemes"][scheme_name]
    
    # Add/update BearerAuth security scheme (only one)
    openapi_schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "JWT access token for authentication. Get token from /auth/verify-otp endpoint. For Swagger: Click 'Authorize' button and paste your token (without 'Bearer ' prefix)."
    }
    
    # Remove CSRF token from security schemes if it exists (no longer shown in Authorize button)
    if "CSRFToken" in openapi_schema["components"]["securitySchemes"]:
        del openapi_schema["components"]["securitySchemes"]["CSRFToken"]
    
    
    # List of public endpoints that don't require authentication
    public_endpoints = {
        "/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/auth/send-otp",
        "/auth/verify-otp",
        "/newsletter/subscribe",
        "/api/tracking/event",  # Tracking endpoint - no CSRF required
    }
    
    # Add security requirements to all protected endpoints
    # Iterate through all paths and add BearerAuth security to non-public endpoints
    if "paths" in openapi_schema:
        for path, methods in openapi_schema["paths"].items():
            # Skip public endpoints
            if path in public_endpoints:
                continue
            
            # Skip auth endpoints (except those that require auth like refresh, logout)
            if path.startswith("/auth/"):
                # Only skip send-otp and verify-otp, others need auth
                if path in ["/auth/send-otp", "/auth/verify-otp"]:
                    continue
            
            # Remove CSRF parameter from exempted endpoints if they exist
            # Exempted: /auth/refresh, newsletter, products, categories, banners, location, order status, webhooks
            is_webhook = "/webhook" in path or path.endswith("/webhook")
            is_product = path.startswith("/products") or path.startswith("/product/")
            is_category = path.startswith("/categories") or path.startswith("/category/")
            is_banner = path.startswith("/banners") or path.startswith("/banner/")
            is_location = path.startswith("/location/") or path.startswith("/api/v1/location/")
            is_order_status = "/status" in path and ("/order" in path or path.startswith("/order"))
            is_exempted = (
                path == "/auth/refresh" or 
                path.startswith("/newsletter/") or 
                is_webhook or 
                is_product or 
                is_category or 
                is_banner or 
                is_location or 
                is_order_status
            )
            if is_exempted:
                for method in methods.keys():
                    if "parameters" in methods[method]:
                        methods[method]["parameters"] = [
                            param for param in methods[method]["parameters"]
                            if param.get("name") != "X-CSRF-Token"
                        ]
            
            # Add security requirement to all HTTP methods for this path
            for method in methods.keys():
                method_lower = method.lower()
                if method_lower in ["get", "post", "put", "delete", "patch"]:
                    # All protected endpoints need BearerAuth for authentication
                    # POST/PUT/DELETE/PATCH also need CSRF token (added as header, not security scheme)
                    # Swagger UI handles BearerAuth via the "Authorize" button
                    # CSRF token must be added manually as X-CSRF-Token header for POST/PUT/DELETE
                    required_security = [{"BearerAuth": []}]
                    
                    # Add CSRF token as header parameter for state-changing operations
                    # Skip CSRF for newsletter endpoints (exempted for anonymous users)
                    # Skip CSRF for /auth/refresh endpoint (uses refresh token for security)
                    # Skip CSRF for webhook endpoints (called by external services)
                    # Skip CSRF for product, category, banner, location endpoints (public/read-only)
                    # Skip CSRF for order status endpoints (used by admin/lab technicians)
                    is_webhook = "/webhook" in path or path.endswith("/webhook")
                    is_product = path.startswith("/products") or path.startswith("/product/")
                    is_category = path.startswith("/categories") or path.startswith("/category/")
                    is_banner = path.startswith("/banners") or path.startswith("/banner/")
                    is_location = path.startswith("/location/") or path.startswith("/api/v1/location/")
                    is_order_status = "/status" in path and ("/order" in path or path.startswith("/order"))
                    should_skip_csrf = (
                        path.startswith("/newsletter/") or 
                        path == "/auth/refresh" or 
                        is_webhook or 
                        is_product or 
                        is_category or 
                        is_banner or 
                        is_location or 
                        is_order_status
                    )
                    if method_lower in ["post", "put", "delete", "patch"] and not should_skip_csrf:
                        # Ensure parameters array exists
                        if "parameters" not in methods[method]:
                            methods[method]["parameters"] = []
                        
                        # Check if X-CSRF-Token parameter already exists
                        has_csrf_param = any(
                            param.get("name") == "X-CSRF-Token" 
                            for param in methods[method].get("parameters", [])
                        )
                        
                        if not has_csrf_param:
                            # Add CSRF token as a header parameter
                            csrf_param = {
                                "name": "X-CSRF-Token",
                                "in": "header",
                                "required": False,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "CSRF token for state-changing operations (OPTIONAL). Get your token from GET /auth/csrf-token endpoint (requires authentication). You can also use the auto-CSRF helper script in the browser console."
                            }
                            methods[method]["parameters"].append(csrf_param)
                        
                        # Add note about CSRF requirement to description
                        if "description" not in methods[method]:
                            methods[method]["description"] = ""
                        csrf_note = "\n\n**ℹ️ CSRF Token (Optional):** You can optionally include a CSRF token in the `X-CSRF-Token` header. Get your token from GET /auth/csrf-token endpoint (requires authentication)."
                        if csrf_note not in methods[method].get("description", ""):
                            methods[method]["description"] = (methods[method].get("description", "") + csrf_note).strip()
                    
                    # Always set security requirements (override any existing)
                    methods[method]["security"] = required_security
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi


def _format_validation_errors(exc):
    """Return consistent error structure for 422 responses."""
    detail_list = []
    errors = exc.errors() if hasattr(exc, "errors") else []

    for err in errors:
        loc = err.get("loc", [])
        source = loc[0] if loc else "body"
        field = loc[-1] if len(loc) > 1 else loc[0] if loc else None
        msg = err.get("msg")
        # Strip "Value error, " prefix from Pydantic validator messages
        if msg and isinstance(msg, str) and msg.startswith("Value error, "):
            msg = msg[len("Value error, "):].strip()
        detail_list.append({
            "source": source,
            "field": field,
            "message": msg,
            "type": err.get("type")
        })
    return detail_list


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle FastAPI request validation errors."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    detail_list = _format_validation_errors(exc)
    
    # Log validation errors with details
    validation_details = ", ".join([
        f"{err.get('field', 'unknown')}: {err.get('message', 'unknown error')}"
        for err in detail_list[:5]  # Limit to first 5 errors to avoid log spam
    ])
    if len(detail_list) > 5:
        validation_details += f" ... and {len(detail_list) - 5} more errors"
    
    log_message = (
        f"❌ {request.method} {request.url.path} | "
        f"Status: 422 (VALIDATION_ERROR) | "
        f"Validation Errors: {validation_details} | "
        f"IP: {client_ip} | "
        f"User-Agent: {user_agent}"
    )
    logger.warning(log_message)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {log_message}")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "status": "error",
            "message": "Request validation failed.",
            "details": detail_list
        }
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    """Handle Pydantic validation errors."""
    client_ip = request.client.host if request.client else "unknown"
    detail_list = _format_validation_errors(exc)
    
    # Log validation errors with details
    validation_details = ", ".join([
        f"{err.get('field', 'unknown')}: {err.get('message', 'unknown error')}"
        for err in detail_list
    ])
    log_message = (
        f"❌ {request.method} {request.url.path} | "
        f"Status: 422 (VALIDATION_ERROR) | "
        f"Validation Errors: {validation_details} | "
        f"IP: {client_ip}"
    )
    logger.warning(log_message)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {log_message}")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "status": "error",
            "message": "Validation failed.",
            "details": detail_list
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTPException errors with detailed logging."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    # Determine status category and emoji
    if 400 <= exc.status_code < 500:
        status_emoji = "❌"
        status_category = "CLIENT_ERROR"
        log_level = logger.warning
    else:
        status_emoji = "💥"
        status_category = "SERVER_ERROR"
        log_level = logger.error
    
    # Extract error detail and error_code
    error_detail = exc.detail
    error_code = None
    error_message = None
    
    if isinstance(error_detail, dict):
        # Handle nested detail structure: {"error_code": "...", "detail": "..."}
        error_code = error_detail.get("error_code")
        error_message = error_detail.get("detail", str(error_detail))
        # If detail is missing but error_code exists, use error_code as message
        if not error_message and error_code:
            error_message = error_code
    elif isinstance(error_detail, str):
        error_message = error_detail
    else:
        error_message = str(error_detail)
    
    # Log HTTPException with details
    log_message = (
        f"{status_emoji} {request.method} {request.url.path} | "
        f"Status: {exc.status_code} ({status_category}) | "
        f"Error: {error_message} | "
        f"IP: {client_ip} | "
        f"User-Agent: {user_agent}"
    )
    if error_code:
        log_message += f" | Error Code: {error_code}"
    log_level(log_message)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {log_message}")
    
    # Return standard HTTPException response with error_code if available
    response_content = {
        "status": "error",
        "message": error_message,
        "status_code": exc.status_code
    }
    if error_code:
        response_content["error_code"] = error_code
    
    return JSONResponse(
        status_code=exc.status_code,
        content=response_content
    )


# CORS configuration
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
if ALLOWED_ORIGINS == ["*"] and ENVIRONMENT == "production":
    warnings.warn("CORS is set to allow all origins. This is not recommended for production.")

# Middleware
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE","PATCH","OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-CSRF-Token", "X-CSRF-TOKEN"],
)
# CSRF protection middleware (must be after CORS)
from Login_module.Utils.csrf_middleware import CSRFProtectionMiddleware
app.add_middleware(CSRFProtectionMiddleware)

# Include routers
app.include_router(otp_router)  # /auth/send-otp, /auth/verify-otp
app.include_router(twilio_router)  # /auth/twilio/send-verification, /auth/twilio/verify
app.include_router(auth_token_router)  # /auth/refresh, /auth/logout, /auth/logout-all (dual-token strategy)
app.include_router(product_router)
app.include_router(category_router)
app.include_router(cart_router)
app.include_router(address_router)
app.include_router(banner_router)
app.include_router(consent_router)
app.include_router(member_router)
app.include_router(order_router)
app.include_router(audit_router)
app.include_router(session_router)
app.include_router(phone_change_router)
app.include_router(newsletter_router)  # /newsletter/subscribe
app.include_router(notification_router)  # /api/notifications
app.include_router(tracking_router)  # /api/tracking/event
app.include_router(utm_tracking_router)  # /api/utm-tracking
app.include_router(account_router)  # /account/feedback
app.include_router(enquiry_router)  # /enquiry (form + POST)
app.include_router(thyrocare_router)  # /thyrocare

# Include Google Meet API router if available
if gmeet_router:
    app.include_router(gmeet_router)
# API Endpoints
@app.get("/")
def root():
    """Root endpoint with API information."""
    return {
        "status": "success",
        "message": "Nucleoseq Unified API",
        "version": "3.0.0",
        "endpoints": {
            "auth": "/auth (send-otp, verify-otp, refresh, logout, logout-all)",
            "products": "/products",
            "categories": "/categories",
            "cart": "/cart",
            "address": "/address",
            "banners": "/banners",
            "consent": "/consent",
            "member": "/member",
            "orders": "/orders",
            "audit": "/audit",
            "sessions": "/sessions",
            "gmeet": "/gmeet",
            "phone-change": "/api/phone-change",
            "location": "/api/v1/location",
            "newsletter": "/newsletter",
            "notifications": "/api/notifications"
        },
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
def health_check():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "service": "Nucleoseq Unified API"
    }


# Run application
if __name__ == "__main__":
    import uvicorn
    import socket
    
    # Configure uvicorn loggers
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.setLevel(logging.INFO)
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(logging.INFO)
    
    # Check if port is available
    def is_port_available(port):
        """Check if a port is available."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return True
            except OSError:
                return False
    
    def find_available_port(start_port, max_attempts=10):
        """Find an available port starting from start_port."""
        for i in range(max_attempts):
            port = start_port + i
            if is_port_available(port):
                return port
        return None
    
    # Get port from environment or use default
    requested_port = int(os.getenv("PORT", 8080))
    
    # Check if requested port is available, if not find next available
    if not is_port_available(requested_port):
        logger.warning(f"Port {requested_port} is already in use!")
        logger.info(f"Searching for next available port starting from {requested_port}...")
        port = find_available_port(requested_port)
        if port is None:
            logger.error(f"Could not find an available port after {requested_port + 10} attempts!")
            logger.error("Please either:")
            logger.error(f"  1. Stop the process using port {requested_port}")
            logger.error("  2. Set a different PORT environment variable (e.g., PORT=8031)")
            logger.error("")
            logger.error("To find and kill the process using the port on Windows:")
            logger.error(f"  netstat -ano | findstr :{requested_port}")
            logger.error("  taskkill /F /PID <PID>")
            sys.exit(1)
        logger.info(f"Using port {port} instead (port {requested_port} was in use)")
    else:
        port = requested_port
    
    print("\n" + "="*60)
    print("Starting Nucleoseq Unified API Server")
    print("="*60)
    print(f"Server will be available at: http://0.0.0.0:{port}")
    print(f"API Documentation: http://0.0.0.0:{port}/docs")
    print(f"ReDoc Documentation: http://0.0.0.0:{port}/redoc")
    print("="*60 + "\n")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
        access_log=True,
        use_colors=True
    )

