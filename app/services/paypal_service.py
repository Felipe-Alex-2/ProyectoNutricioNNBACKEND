"""
PayPal REST API client — Sandbox mode.

Handles OAuth2 authentication and order management
(create + capture) using PayPal's v2 Checkout Orders API.
"""

import base64
import logging
from typing import Any, Dict, Optional
import httpx
from app.config import settings

logger = logging.getLogger("paypal")


class PayPalService:
    """Stateless helper that talks to the PayPal REST API."""

    # ------------------------------------------------------------------ #
    #  OAuth2 — get an access token using client_credentials grant
    # ------------------------------------------------------------------ #
    @staticmethod
    def _get_access_token() -> str:
        """Obtain a short-lived Bearer token from PayPal."""
        credentials = f"{settings.PAYPAL_CLIENT_ID}:{settings.PAYPAL_CLIENT_SECRET}"
        encoded = base64.b64encode(credentials.encode()).decode()

        response = httpx.post(
            f"{settings.PAYPAL_BASE_URL}/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=30.0,
        )

        if response.status_code != 200:
            logger.error(f"PayPal OAuth failed: {response.status_code} — {response.text}")
            raise Exception(f"PayPal authentication failed: {response.text}")

        return response.json()["access_token"]

    # ------------------------------------------------------------------ #
    #  Create Order — initiates a checkout session
    # ------------------------------------------------------------------ #
    @staticmethod
    def create_order(
        amount: float,
        currency: str = "USD",
        description: str = "Suscripción NutriSalud",
        return_url: str = "http://localhost:4200/paypal-return",
        cancel_url: str = "http://localhost:4200/dashboard",
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a PayPal order and return the order_id + approval_url.

        The caller redirects the buyer to `approval_url`; after the buyer
        approves, PayPal redirects them back to `return_url?token=<order_id>`.
        """
        access_token = PayPalService._get_access_token()

        # Sanitize description to clean ASCII
        clean_desc = (description or "Servicio NutriSalud").encode("ascii", "ignore").decode("ascii").strip()
        if not clean_desc:
            clean_desc = "Servicio NutriSalud"

        # Modern PayPal v2 Orders payload using payment_source
        order_payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": currency,
                        "value": f"{amount:.2f}",
                    },
                    "description": clean_desc[:127],
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "NutriSalud POS",
                        "landing_page": "LOGIN",
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    }
                }
            },
        }

        response = httpx.post(
            f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                **({"PayPal-Request-Id": request_id} if request_id else {}),
            },
            json=order_payload,
            timeout=30.0,
        )

        if response.status_code not in (200, 201):
            logger.error(f"PayPal create order failed: {response.status_code} — {response.text}")
            raise Exception(f"Error creating PayPal order: {response.text}")

        data = response.json()
        order_id = data["id"]

        # Find approval or payer-action link
        approval_url: Optional[str] = None
        for link in data.get("links", []):
            if link.get("rel") in ("payer-action", "approve"):
                approval_url = link.get("href")
                break

        if not approval_url:
            # Fallback URL if links not found
            approval_url = f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}"

        # Ensure fundingSource=paypal is present so it directs directly to PayPal login
        if "fundingSource=" not in approval_url:
            separator = "&" if "?" in approval_url else "?"
            approval_url = f"{approval_url}{separator}fundingSource=paypal"

        logger.info(f"PayPal order created: {order_id} -> {approval_url}")
        return {"order_id": order_id, "approval_url": approval_url}

    # ------------------------------------------------------------------ #
    #  Capture Order — finalize payment after buyer approval
    # ------------------------------------------------------------------ #
    @staticmethod
    def capture_order(order_id: str) -> Dict[str, Any]:
        """
        Capture a previously approved PayPal order.

        Returns the capture details including capture_id and status.
        """
        access_token = PayPalService._get_access_token()

        response = httpx.post(
            f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": f"capture-{order_id}",
            },
            timeout=30.0,
        )

        if response.status_code not in (200, 201):
            logger.error(f"PayPal capture failed: {response.status_code} — {response.text}")
            raise Exception(f"Error capturing PayPal order: {response.text}")

        data = response.json()
        status = data.get("status", "UNKNOWN")

        # Extract capture ID from the first purchase unit
        capture_id: Optional[str] = None
        purchase_units = data.get("purchase_units", [])
        if purchase_units:
            captures = purchase_units[0].get("payments", {}).get("captures", [])
            if captures:
                capture_id = captures[0].get("id")

        logger.info(f"PayPal order {order_id} captured — status: {status}, capture_id: {capture_id}")
        return {
            "order_id": order_id,
            "capture_id": capture_id,
            "status": status,
            "raw": data,
        }

    # ------------------------------------------------------------------ #
    #  Get Order details — query order status
    # ------------------------------------------------------------------ #
    @staticmethod
    def get_order(order_id: str) -> Dict[str, Any]:
        """
        Get details and status of a PayPal order.
        """
        access_token = PayPalService._get_access_token()

        response = httpx.get(
            f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        if response.status_code not in (200, 201):
            logger.error(f"PayPal get order failed: {response.status_code} — {response.text}")
            raise Exception(f"Error consultando orden PayPal: {response.text}")

        return response.json()


    @staticmethod
    def validate_completed_payment(order, expected_amount, expected_currency):
        from decimal import Decimal, InvalidOperation
        try:
            units = order["purchase_units"]
            captures = units[0]["payments"]["captures"]
            if len(units) != 1 or len(captures) != 1:
                raise ValueError("Cantidad inesperada de transacciones.")
            capture = captures[0]
            if order.get("status") != "COMPLETED" or capture.get("status") != "COMPLETED" or not capture.get("id"):
                raise ValueError("PayPal no confirmó un cobro completado.")
            amount = capture["amount"]
            if Decimal(amount["value"]) != Decimal(str(expected_amount)) or amount["currency_code"] != expected_currency:
                raise ValueError("El importe o moneda no coincide con la orden.")
            return capture
        except (KeyError, IndexError, TypeError, InvalidOperation):
            raise ValueError("Respuesta de cobro PayPal incompleta.")
