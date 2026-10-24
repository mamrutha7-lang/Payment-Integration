"""Razorpay, over httpx -- no SDK, so no new dependency to pin.

Three things are needed and all three are here: open an order, verify the
signature Checkout hands the browser, and verify the signature on a webhook.
Both signatures are HMAC-SHA256 hex, compared in constant time.

The two signatures sign DIFFERENT messages with DIFFERENT secrets, and mixing
them up verifies nothing:

    checkout   HMAC(key_secret,     f"{order_id}|{payment_id}")
    webhook    HMAC(webhook_secret, raw request body)
"""

import hashlib
import hmac

import httpx

from .config import settings

ORDERS_URL = "https://api.razorpay.com/v1/orders"


class RazorpayError(Exception):
    """Razorpay refused, or could not be reached."""


def configured() -> bool:
    return bool(settings.razorpay_key_id.strip() and settings.razorpay_key_secret.strip())


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    if not configured() or not signature:
        return False
    expected = _hmac_hex(settings.razorpay_key_secret, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    secret = settings.razorpay_webhook_secret.strip()
    # A blank secret must REFUSE, not verify: HMAC with an empty key is a valid
    # computation anyone can reproduce.
    if not secret or not signature:
        return False
    return hmac.compare_digest(_hmac_hex(secret, body), signature)


def create_order(*, amount_paise: int, currency: str, receipt: str, notes: dict[str, str]) -> dict:
    """POST /v1/orders. ``receipt`` is capped at 40 characters by Razorpay."""
    try:
        r = httpx.post(
            ORDERS_URL,
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={
                "amount": amount_paise,
                "currency": currency,
                "receipt": receipt[:40],
                "notes": notes,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise RazorpayError("Could not reach Razorpay.") from exc
    if r.status_code != 200:
        raise RazorpayError(f"Razorpay refused the order ({r.status_code}).")
    return r.json()
