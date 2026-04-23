# Nucleotide Website — CLAUDE.md

## Overview

FastAPI backend for the Thyrocare Nucleotide medical platform. Provides REST APIs for blood-test ordering, user authentication, payment processing, appointment booking, and push notifications. Consumed by web and mobile clients.

## Tech Stack

- **Runtime:** Python 3.13, FastAPI 0.104+, Uvicorn (ASGI)
- **Database:** MySQL via SQLAlchemy 2.0 + PyMySQL; Alembic for migrations
- **Caching / Sessions:** Redis
- **Validation:** Pydantic v2 (`pydantic-settings` for config)
- **Auth:** JWT (PyJWT), bcrypt, AES-256 phone encryption
- **Task Scheduling:** APScheduler
- **Deployment:** Docker → AWS App Runner

## Key Directories

| Path | Purpose |
|---|---|
| `main.py` | App entry point; lifespan handler, middleware, router registration |
| `config.py` | Pydantic `Settings` — all env vars with defaults |
| `database.py` | SQLAlchemy engine + `SessionLocal` factory |
| `deps.py` | `get_db()` session dependency |
| `Login_module/` | Auth: OTP, JWT tokens, device sessions, Twilio SMS, shared utilities |
| `Login_module/Utils/` | Cross-cutting: auth deps, security, IST timestamps, rate limiting, CSRF |
| `Orders_module/` | Order creation, Razorpay payment, status tracking |
| `Product_module/` | Product catalog and categories |
| `Cart_module/` | Shopping cart, coupon validation |
| `Address_module/` | User addresses, pincode/serviceable-location validation |
| `Member_module/` | Family member profiles, S3 photo uploads |
| `Thyrocare_module/` | Thyrocare blood-test API integration |
| `gmeet_api/` | Google Meet/Calendar appointment booking for counselors |
| `Notification_module/` | Firebase FCM push notifications |
| `Banner_module/` | S3-backed marketing banners |
| `Consent_module/` | User consent records |
| `Tracking_module/` | Analytics event tracking |
| `Utm_tracking_module/` | UTM parameter capture |
| `Audit_module/` | Profile change audit trail |
| `alembic/` | DB migration scripts (auto-run on startup) |
| `tests/` | Pytest unit tests |

## Build / Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run dev server (with reload)
uvicorn main:app --reload --host 0.0.0.0 --port 8030

# Run DB migrations
python alembic_runner.py

# Create all tables (fresh DB only)
python create_all_tables.py

# Run tests
pytest tests/

# Docker
docker build -t nucleotide:latest .
docker run -p 8080:8080 --env-file .env nucleotide:latest
```

## Integration Services

| Service | Purpose |
|---|---|
| Thyrocare API | Blood-test catalog + order booking |
| Razorpay | Payment processing + webhook verification |
| Firebase FCM | Push notifications |
| Twilio | SMS OTP verification |
| Google Calendar / Meet | Counselor appointment scheduling |
| AWS S3 | Member photos, banners, test reports |

## API Docs (local)

- Swagger UI: `http://localhost:8030/docs`
- ReDoc: `http://localhost:8030/redoc`
- Health check: `GET /health`

## Environment

All configuration is driven by `.env` (see `config.py` for the full list of required variables). `additional_env.txt` documents AWS and OAuth variables.

## Additional Documentation

| File | When to read |
|---|---|
| [.claude/docs/architectural_patterns.md](.claude/docs/architectural_patterns.md) | Before adding/modifying any module — covers conventions for models, routers, CRUD, schemas, auth, audit, and timestamps |
| [README.md](README.md) | Full project narrative and deployment overview |
| [FRONTEND_INTEGRATION_GUIDE.md](FRONTEND_INTEGRATION_GUIDE.md) | API endpoint specs for client integration |
| [TRACKING_API_RESPONSES.md](TRACKING_API_RESPONSES.md) | Tracking endpoint response shapes |
