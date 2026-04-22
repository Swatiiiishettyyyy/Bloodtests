"""
Thyrocare B2C API service.
Handles authentication (login/logout) with automatic token refresh,
and provides methods for all Thyrocare API calls.
"""
import requests
import logging
import time
from typing import Optional, Dict, Any
from config import settings

logger = logging.getLogger(__name__)


import threading

class ThyrocareService:
    _instance = None
    _token: Optional[str] = None
    _token_expiry: float = 0
    _login_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ThyrocareService, cls).__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _get_headers(self, include_auth: bool = True) -> Dict[str, str]:
        headers = {
            "Partner-Id": settings.THYROCARE_PARTNER_ID,
            "Request-Id": settings.THYROCARE_REQUEST_ID,
            "Client-Type": settings.THYROCARE_CLIENT_TYPE,
            "Entity-Type": settings.THYROCARE_ENTITY_TYPE,
            "Content-Type": "application/json",
        }
        if include_auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def login(self) -> bool:
        """Authenticate with Thyrocare and store the JWT token."""
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/auth/login"
        payload = {
            "username": settings.THYROCARE_USERNAME,
            "password": settings.THYROCARE_PASSWORD,
        }
        try:
            logger.info(f"Attempting Thyrocare login for user: {settings.THYROCARE_USERNAME}")
            response = requests.post(
                url, json=payload, headers=self._get_headers(include_auth=False)
            )
            response.raise_for_status()
            data = response.json()
            self._token = data.get("token")
            if not self._token:
                logger.error("Thyrocare login response did not include a token. Response keys: %s", list(data.keys()))
                self._token = None
                self._token_expiry = 0
                return False

            # Decode JWT expiry; fall back to 24 h if parsing fails
            try:
                import jwt
                decoded = jwt.decode(self._token, options={"verify_signature": False})
                self._token_expiry = decoded.get("exp", time.time() + 86400)
            except Exception as jwt_err:
                logger.warning("Could not decode Thyrocare JWT expiry (%s); defaulting to 24h", jwt_err)
                self._token_expiry = time.time() + 86400

            print(f"[THYROCARE AUTH] Token acquired successfully. Expires at: {self._token_expiry}")
            logger.info("Thyrocare login successful. Token acquired.")
            return True
        except Exception as e:
            print(f"[THYROCARE AUTH] Login FAILED: {str(e)}")
            logger.error(f"Thyrocare login failed: {str(e)}")
            self._token = None
            self._token_expiry = 0
            return False

    def logout(self) -> bool:
        """Logout from Thyrocare session."""
        if not self._token:
            return True
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/auth/logout"
        try:
            response = requests.post(url, headers=self._get_headers())
            response.raise_for_status()
            self._token = None
            self._token_expiry = 0
            logger.info("Thyrocare logout successful.")
            return True
        except Exception as e:
            logger.error(f"Thyrocare logout failed: {str(e)}")
            return False

    def get_token(self) -> Optional[str]:
        """Return a valid token, re-logging in if missing or expiring within 5 minutes."""
        if not self._token or time.time() > (self._token_expiry - 300):
            with ThyrocareService._login_lock:
                # Double-check after acquiring lock (another thread may have refreshed)
                if not self._token or time.time() > (self._token_expiry - 300):
                    if not self.login():
                        return None
        return self._token

    def _auth_headers(self) -> Dict[str, str]:
        """Build headers with a fresh token, raising RuntimeError if unavailable."""
        token = self.get_token()
        if not token:
            raise RuntimeError("Unable to obtain Thyrocare auth token")
        headers = self._get_headers(include_auth=False)
        headers["Authorization"] = f"Bearer {token}"
        return headers

    # ------------------------------------------------------------------
    # Thyrocare API methods
    # ------------------------------------------------------------------

    def get_price_breakup(self, product_codes: list[str], pincode: str) -> Dict[str, Any]:
        """
        Fetch price breakup for a list of product codes at a given pincode.
        POST /partners/v1/products/price-breakup
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/products/price-breakup"
        payload = {
            "productCodes": product_codes,
            "pincode": pincode,
            "payType": settings.THYROCARE_PAY_TYPE,
        }
        try:
            response = requests.post(url, json=payload, headers=self._auth_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            logger.error(f"Thyrocare price-breakup failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare price-breakup error: {e}")
            raise

    def create_order(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Book a home-collection order on Thyrocare.
        POST /partners/v1/orders
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/orders"
        try:
            response = requests.post(url, json=order_payload, headers=self._auth_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            logger.error(f"Thyrocare create-order failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare create-order error: {e}")
            raise

    def get_report(self, thyrocare_order_id: str, lead_id: str, report_type: str = "pdf") -> Dict[str, Any]:
        """
        Fetch diagnostic report URL for a patient.
        Tries GET /partners/v1/{orderId}/reports/{leadId} then
        /partners/v1/orders/{orderId}/reports/{leadId} (404 on the first is retried).
        Returns JSON that typically includes a short-lived report URL.
        """
        params = {"type": report_type}
        headers = self._auth_headers()
        paths = (
            f"{settings.THYROCARE_BASE_URL}/partners/v1/{thyrocare_order_id}/reports/{lead_id}",
            f"{settings.THYROCARE_BASE_URL}/partners/v1/orders/{thyrocare_order_id}/reports/{lead_id}",
        )
        last_http: Optional[requests.HTTPError] = None
        for url in paths:
            try:
                response = requests.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.HTTPError as e:
                last_http = e
                if e.response is not None and e.response.status_code == 404:
                    continue
                logger.error(
                    "Thyrocare get-report failed [%s]: %s",
                    e.response.status_code if e.response else "?",
                    e.response.text if e.response else "",
                )
                raise
            except Exception as e:
                logger.error(f"Thyrocare get-report error: {e}")
                raise
        if last_http is not None:
            logger.error(
                "Thyrocare get-report failed [%s]: %s",
                last_http.response.status_code if last_http.response else "?",
                last_http.response.text if last_http.response else "",
            )
            raise last_http
        raise RuntimeError("Thyrocare get-report: no request was made")

    def get_order_details(self, thyrocare_order_id: str, include: str = "tracking,items,price") -> Dict[str, Any]:
        """
        Fetch full order details from Thyrocare.
        GET /partners/v1/orders/{orderId}?include=tracking,items,price
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/orders/{thyrocare_order_id}"
        params = {"include": include}
        try:
            response = requests.get(url, params=params, headers=self._auth_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            logger.error(f"Thyrocare get-order-details failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare get-order-details error: {e}")
            raise

    def get_order_status(self, thyrocare_order_id: str) -> Dict[str, Any]:
        """
        Fetch the current status of a Thyrocare order.
        GET /partners/v1/orders/{orderId}/status
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/orders/{thyrocare_order_id}/status"
        try:
            response = requests.get(url, headers=self._auth_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            logger.error(f"Thyrocare get-order-status failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare get-order-status error: {e}")
            raise

    def cancel_order(self, thyrocare_order_id: str, reason: str = "") -> Dict[str, Any]:
        """
        Cancel a Thyrocare order.
        POST /partners/v1/orders/{orderId}/cancel
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/orders/{thyrocare_order_id}/cancel"
        payload = {"reason": reason}
        try:
            response = requests.post(url, json=payload, headers=self._auth_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            logger.error(f"Thyrocare cancel-order failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare cancel-order error: {e}")
            raise

    def get_slots(self, pincode: str, date: str, patients: list = None) -> Dict[str, Any]:
        """
        Fetch available collection slots for a pincode, date and patients.
        POST /partners/v1/slots/search
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/slots/search"
        payload = {
            "appointmentDate": date,
            "pincode": pincode,
            "patients": patients or []
        }
        try:
            response = requests.post(url, json=payload, headers=self._auth_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            logger.error(f"Thyrocare get-slots failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare get-slots error: {e}")
            raise

    def get_serviceable_pincodes(self) -> list:
        """
        Fetch all serviceable pincodes from Thyrocare.
        GET /partners/v1/serviceability/pincodes
        Returns list of pincode integers.
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/serviceability/pincodes"
        try:
            response = requests.get(url, headers=self._auth_headers())
            response.raise_for_status()
            data = response.json()
            # Response: {"serviceTypes": [{"type": "ALL", "pincodes": [110001, ...]}]}
            pincodes = []
            for service_type in data.get("serviceTypes", []):
                pincodes.extend(service_type.get("pincodes", []))
            return list(set(pincodes))
        except requests.HTTPError as e:
            logger.error(f"Thyrocare get-serviceable-pincodes failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare get-serviceable-pincodes error: {e}")
            raise

    def get_catalogue(self, min_price: float = 0, max_price: float = 10000, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        """
        Fetch available products from Thyrocare catalogue.
        GET /partners/v1/catalog/products
        """
        url = f"{settings.THYROCARE_BASE_URL}/partners/v1/catalog/products"
        params = {
            "minPrice": int(min_price),
            "maxPrice": int(max_price),
            "page": page,
            "pageSize": page_size,
        }
        try:
            response = requests.get(url, params=params, headers=self._auth_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            logger.error(f"Thyrocare get-catalogue failed [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Thyrocare get-catalogue error: {e}")
            raise


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def start_thyrocare_auth_task():
    """
    Initializes Thyrocare authentication at app startup.
    The singleton token is reused for all subsequent API calls;
    get_token() handles automatic re-login when the token is near expiry.
    """
    service = ThyrocareService()
    success = service.login()
    if success:
        logger.info("Thyrocare background auth initialized.")
    else:
        logger.error("Failed to initialize Thyrocare background auth.")


def normalize_thyrocare_errors(response_data: Any) -> Dict[str, Any]:
    """Helper to extract error messages from Thyrocare responses."""
    if isinstance(response_data, dict):
        errors = response_data.get("errors", [])
        if errors and isinstance(errors, list):
            return {"message": errors[0].get("message", "Unknown Thyrocare error"), "raw": response_data}
    return {"message": str(response_data), "raw": response_data}
