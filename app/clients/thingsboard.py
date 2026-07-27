import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx

from app.auth.security import assert_allowed_tb_url
from app.config import Settings

logger = logging.getLogger(__name__)

# ThingsBoard page APIs return one page plus a `hasNext` cursor. Requesting a single
# page silently truncates: BOI alone has 104 leaf devices against a 100-row page.
# API-TB.md: "Always paginate large datasets".
_MAX_PAGES = 50  # ponytail: 5k devices at the default page size; raise if a fleet outgrows it


def require_uuid(value: str, label: str = "id") -> str:
    """IDs come from JWT claims and URL paths; validate before path interpolation."""
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{label} is not a valid UUID") from exc
    return value


async def fetch_all_pages(
    get: Callable[[str, dict[str, Any]], Awaitable[Any]], path: str, page_size: int
) -> Any:
    """Follow TB's `hasNext` cursor and return one merged page-shaped body.

    The merged body keeps the last page's other fields so callers that only read
    `data` (and any that check `hasNext`) keep working unchanged.
    """
    rows: list[Any] = []
    body: Any = None
    for page in range(_MAX_PAGES):
        body = await get(path, {"pageSize": page_size, "page": page})
        if not isinstance(body, dict):
            return body if page == 0 else {"data": rows, "hasNext": False}
        rows.extend(body.get("data") or [])
        if not body.get("hasNext"):
            break
    else:
        # Exhausting the cap means the result IS truncated — say so rather than
        # handing back a short list wearing hasNext=False, which is the exact
        # silent-drop this function exists to remove.
        logger.warning("[TB] %s hit the %d-page cap; result is truncated", path, _MAX_PAGES)
    return {**body, "data": rows, "hasNext": False} if isinstance(body, dict) else {"data": rows}


class ThingsBoardClient:
    def __init__(self, settings: Settings) -> None:
        assert_allowed_tb_url(settings.tb_url, settings)
        self.settings = settings
        self.http = httpx.AsyncClient(base_url=settings.tb_url.rstrip("/"), timeout=15)
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    async def _headers(self) -> dict[str, str]:
        if not self._token:
            async with self._login_lock:
                if not self._token:  # re-check after waiting on the lock
                    response = await self.http.post(
                        "/api/auth/login",
                        json={
                            "username": self.settings.tb_user,
                            "password": self.settings.tb_password,
                        },
                    )
                    response.raise_for_status()
                    self._token = response.json()["token"]
        return {"X-Authorization": f"Bearer {self._token}"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.http.get(path, params=params, headers=await self._headers())
        if response.status_code == 401:
            self._token = None
            response = await self.http.get(path, params=params, headers=await self._headers())
        response.raise_for_status()
        return response.json()

    async def devices(self, customer_id: str, page_size: int = 100) -> Any:
        require_uuid(customer_id, "customer_id")
        return await fetch_all_pages(
            self._get, f"/api/customer/{customer_id}/devices", page_size
        )

    async def telemetry(
        self,
        device_id: str,
        keys: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> Any:
        require_uuid(device_id, "device_id")
        params = {
            k: v
            for k, v in {"keys": keys, "startTs": start_ts, "endTs": end_ts}.items()
            if v is not None
        }
        return await self._get(
            f"/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries", params
        )

    async def attributes(self, device_id: str, scope: str) -> Any:
        require_uuid(device_id, "device_id")
        return await self._get(
            f"/api/plugins/telemetry/DEVICE/{device_id}/values/attributes/{scope}"
        )


class UserAwareThingsBoardClient:
    """ThingsBoard client that uses the CALLER's token instead of service login."""

    def __init__(self, settings: Settings, user_token: str) -> None:
        assert_allowed_tb_url(settings.tb_url, settings)
        self.settings = settings
        self.http = httpx.AsyncClient(base_url=settings.tb_url.rstrip("/"), timeout=15)
        self._user_token = user_token

    async def close(self) -> None:
        await self.http.aclose()

    def _headers(self) -> dict[str, str]:
        return {"X-Authorization": f"Bearer {self._user_token}"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.http.get(path, params=params, headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def devices(self, customer_id: str, page_size: int = 100) -> Any:
        require_uuid(customer_id, "customer_id")
        return await fetch_all_pages(
            self._get, f"/api/customer/{customer_id}/devices", page_size
        )

    async def telemetry(
        self,
        device_id: str,
        keys: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> Any:
        require_uuid(device_id, "device_id")
        params = {
            k: v
            for k, v in {"keys": keys, "startTs": start_ts, "endTs": end_ts}.items()
            if v is not None
        }
        return await self._get(
            f"/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries", params
        )

    async def attributes(self, device_id: str, scope: str) -> Any:
        require_uuid(device_id, "device_id")
        return await self._get(
            f"/api/plugins/telemetry/DEVICE/{device_id}/values/attributes/{scope}"
        )