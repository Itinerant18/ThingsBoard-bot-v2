import hashlib
import hmac
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from app.config import Settings


async def verify_webhook(request: Request, settings: Settings) -> bytes:
    body = await request.body()
    if settings.require_webhook_hmac and not settings.webhook_hmac_secret:
        raise HTTPException(status_code=503, detail="Webhook HMAC required but not configured")
    if not settings.webhook_hmac_secret:
        return body  # guard off: secret unset and not required (local dev)
    signature = request.headers.get("X-HMAC-SHA256", "")
    timestamp = request.headers.get("X-Timestamp", "")
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Webhook signature required")
    try:
        skew = abs(int(time.time() * 1000) - int(timestamp))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid webhook timestamp") from exc
    expected = hmac.new(
        settings.webhook_hmac_secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if skew > settings.webhook_max_skew_ms or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    return body


def assert_allowed_tb_url(url: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.hostname not in settings.allowed_tb_hosts()
    ):
        raise ValueError("ThingsBoard URL is not in TB_ALLOWED_HOSTS")
