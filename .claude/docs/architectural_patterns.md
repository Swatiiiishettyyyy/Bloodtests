# Architectural Patterns

Patterns that appear across multiple modules in this codebase.

---

## 1. Module Structure Convention

Every domain module is a package with a consistent file layout:

```
{Name}_module/
├── {Name}_model.py      # SQLAlchemy ORM model(s)
├── {Name}_router.py     # FastAPI route handlers
├── {Name}_schema.py     # Pydantic request/response schemas
├── {Name}_crud.py       # Business logic / DB operations (when non-trivial)
├── {Name}_service.py    # External service wrapper (when applicable)
├── {Name}_audit_model.py  # Audit table (when mutations need tracking)
└── __init__.py
```

Routers are registered centrally in [main.py:616-640](../../main.py#L616).

---

## 2. Session-per-Request (SQLAlchemy)

All routers receive a DB session via FastAPI's dependency system. Never create `SessionLocal()` directly inside a router.

```python
# deps.py:5-10
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Usage in every router: `db: Session = Depends(get_db)`

---

## 3. Standardized Response Envelope

All endpoints return a consistent JSON shape:

```python
return {
    "status": "success",   # or "error"
    "message": "Human-readable description.",
    "data": <payload>,     # omitted or None on errors
}
```

See [Product_module/Product_router.py:47-55](../../Product_module/Product_router.py#L47) for a representative example.

---

## 4. Pydantic Schema Split

Each module has separate request and response schemas. Response models always set `from_attributes = True` so SQLAlchemy ORM instances serialize directly.

```python
class ProductCreate(BaseModel):   # request — no ORM mapping needed
    Name: str
    Price: float

class ProductResponse(BaseModel): # response — maps from ORM
    ProductId: int
    Name: str
    model_config = ConfigDict(from_attributes=True)
```

See [Product_module/Product_schema.py](../../Product_module/Product_schema.py).

---

## 5. Dependency Injection for Auth

Protected endpoints declare the user dependency; the function handles both cookie (web) and `Authorization` header (mobile) automatically.

```python
# Protected endpoint pattern
@router.get("/me")
def get_me(
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ...
```

`get_current_user` lives in [Login_module/Utils/auth_user.py](../../Login_module/Utils/auth_user.py). A variant `get_current_member` validates family-member scoped requests.

---

## 6. CRUD Layer Pattern

Non-trivial business logic belongs in `*_crud.py`, not in the router. Routers stay thin: validate input, call CRUD, return envelope.

```
Router  →  *_crud.py  →  SQLAlchemy session
```

See [Orders_module/Order_crud.py](../../Orders_module/Order_crud.py) for a complex example (order creation, payment verification, status updates).

---

## 7. Service Layer for External Integrations

Each third-party system has its own `*_service.py` that owns the HTTP client, credentials, and error handling. Never call external APIs directly from a router.

| Service file | Integration |
|---|---|
| `Orders_module/razorpay_service.py` | Razorpay payments |
| `Notification_module/firebase_service.py` | Firebase FCM |
| `Address_module/pincode_service.py` | Pincode/location validation |
| `Thyrocare_module/thyrocare_service.py` | Thyrocare blood-test API |
| `Banner_module/Banner_s3_service.py` | AWS S3 uploads |
| `gmeet_api/google_calendar_service.py` | Google Calendar/Meet |

---

## 8. Soft Deletes

Records are never hard-deleted. Use `is_deleted = True` + `deleted_at` timestamp. All queries must filter `Model.is_deleted == False`.

```python
# Product_module/Product_model.py:46-47
is_deleted = Column(Boolean, nullable=False, default=False, index=True)
deleted_at = Column(DateTime(timezone=True), nullable=True)
```

---

## 9. Audit Trail

Modules where data mutations need traceability have a paired audit model and CRUD. The audit record is written on every create/update inside the same DB transaction.

Modules with audit: `Address_module`, `Cart_module`, `Audit_module` (profiles), `Login_module` (tokens).

Pattern: `{Name}_audit_model.py` defines the table; `{Name}_audit_crud.py` provides `record_*` helper functions called from the main CRUD.

---

## 10. IST Timestamp Utility

All `created_at` / `updated_at` column values must use `now_ist()`. Never use `datetime.utcnow()` or `datetime.now()`.

```python
from Login_module.Utils.datetime_utils import now_ist

created_at = Column(DateTime(timezone=True), default=now_ist)
```

Source: [Login_module/Utils/datetime_utils.py](../../Login_module/Utils/datetime_utils.py).

---

## 11. Dual-Token Auth Strategy

- **Access token** — short-lived JWT (default 15 min, configurable); stateless validation
- **Refresh token** — DB-backed; allows rotation and revocation
- **Web** — both tokens delivered/read via HttpOnly cookies
- **Mobile** — tokens delivered in JSON response body; sent in `Authorization: Bearer` header

Config lives in [config.py:10-14](../../config.py#L10).
Implementation: [Login_module/Token/Auth_token_router.py](../../Login_module/Token/Auth_token_router.py).

---

## 12. Enum for Status Fields

Status/type columns use `str(enum.Enum)` subclasses so values are stored as strings in MySQL and are directly comparable without mapping.

```python
class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
```

See `Orders_module/Order_model.py` and `Product_module/Product_model.py` (`PlanType`).
