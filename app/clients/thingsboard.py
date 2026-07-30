import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx

from app.auth.security import assert_allowed_tb_url
from app.config import Settings

logger = logging.getLogger(__name__)

# ThingsBoard page APIs return one page plus a `hasNext` cursor; requesting a single
# page silently truncates. API-TB.md: "Always paginate large datasets".
#
# Measured against live TB (2026-07-27): the largest customer, BOI-MALDATOWN, holds
# exactly 100 devices — sitting ON the default page boundary, not past it. So this is
# preventive, not corrective: nothing is being dropped today, but device 101 would
# have vanished with no error. (BOI's 104 hierarchy leaves span five TB customers;
# they are not one customer's device list.)
_MAX_PAGES = 50  # ponytail: 5k devices at the default page size; raise if a fleet outgrows it
_MAX_ALARM_PAGES = 5  # 500 alarms/device bounds a fleet-wide history request
# The tenant audit log holds tens of thousands of rows and only a fraction survive
# per-caller filtering, so the page cap is generous and truncation is DISCLOSED —
# answering "no entries" because the match sat past the cap is a wrong answer that
# looks like a right one.
_MAX_AUDIT_PAGES = 20


def require_uuid(value: str, label: str = "id") -> str:
    """IDs come from JWT claims and URL paths; validate before path interpolation."""
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{label} is not a valid UUID") from exc
    return value


async def fetch_all_pages(
    get: Callable[[str, dict[str, Any]], Awaitable[Any]],
    path: str,
    page_size: int,
    max_pages: int = _MAX_PAGES,
) -> Any:
    """Follow TB's `hasNext` cursor and return one merged page-shaped body.

    The merged body keeps the last page's other fields so callers that only read
    `data` (and any that check `hasNext`) keep working unchanged.
    """
    rows: list[Any] = []
    body: Any = None
    truncated = False
    for page in range(max_pages):
        body = await get(path, {"pageSize": page_size, "page": page})
        if not isinstance(body, dict):
            return body if page == 0 else {"data": rows, "hasNext": False}
        rows.extend(body.get("data") or [])
        if not body.get("hasNext"):
            break
    else:
        # Exhausting the cap means the result IS truncated — say so rather than
        # handing back a short list wearing hasNext=False, which is the exact
        # silent-drop this function exists to remove. The flag is carried in the body
        # so a caller can disclose it to the user, not merely log it.
        logger.warning("[TB] %s hit the %d-page cap; result is truncated", path, max_pages)
        truncated = True
    if isinstance(body, dict):
        return {**body, "data": rows, "hasNext": False, "truncated": truncated}
    return {"data": rows, "truncated": truncated}


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

    async def audit_logs(
        self, start_ts: int, end_ts: int, page_size: int = 100, max_pages: int = _MAX_AUDIT_PAGES
    ) -> Any:
        """Tenant-wide audit log for a time range.

        ThingsBoard exposes audit only at tenant scope — there is no per-customer
        endpoint — so this returns EVERY customer's activity and the caller must
        filter it. app/query/audit.py is the only thing allowed to consume it, and
        does so against an allow-list built from the end user's own token.
        """

        async def get(path: str, params: dict[str, Any]) -> Any:
            return await self._get(
                path,
                {
                    **params,
                    "startTime": start_ts,
                    "endTime": end_ts,
                    "sortProperty": "createdTime",
                    "sortOrder": "DESC",
                },
            )

        return await fetch_all_pages(get, "/api/audit/logs", page_size, max_pages=max_pages)

    async def alarms(self, device_id: str, page_size: int = 100) -> Any:
        """Alarm history for one device, including active and cleared alarms."""
        require_uuid(device_id, "device_id")

        async def get(path: str, params: dict[str, Any]) -> Any:
            return await self._get(
                path,
                {
                    **params,
                    "searchStatus": "ANY",
                    "sortProperty": "createdTime",
                    "sortOrder": "DESC",
                },
            )

        return await fetch_all_pages(
            get,
            f"/api/alarm/DEVICE/{device_id}",
            page_size,
            max_pages=_MAX_ALARM_PAGES,
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

    async def current_user(self) -> Any:
        """ThingsBoard's own answer to "who is this token?".

        Authoritative identity: a 200 proves the CALLER's token is valid (signature,
        expiry, session) without this service holding any signing key, and the
        returned `authority` is ThingsBoard's verdict rather than a self-asserted
        `scopes` claim — the distinction the Java build got wrong.

        This lives on the user-aware client ONLY. On the service client it would
        describe the service account, which is precisely the wrong answer.
        """
        return await self._get("/api/auth/user")

    async def tenant_devices(self, page_size: int = 100) -> Any:
        """Every device in the tenant. ThingsBoard returns 403 unless the CALLER is
        really a tenant admin — verified empirically against production — which is
        what makes it safe to call on their behalf."""
        return await fetch_all_pages(self._get, "/api/tenant/devices", page_size)

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

    async def audit_logs(
        self, start_ts: int, end_ts: int, page_size: int = 100, max_pages: int = _MAX_AUDIT_PAGES
    ) -> Any:
        """Audit log under the CALLER's token. ThingsBoard returns 403 unless they are
        really a tenant admin, so this needs no filtering — TB has already done it."""

        async def get(path: str, params: dict[str, Any]) -> Any:
            return await self._get(
                path,
                {
                    **params,
                    "startTime": start_ts,
                    "endTime": end_ts,
                    "sortProperty": "createdTime",
                    "sortOrder": "DESC",
                },
            )

        return await fetch_all_pages(get, "/api/audit/logs", page_size, max_pages=max_pages)

    async def customer_users(self, customer_id: str, page_size: int = 100) -> Any:
        """Users assigned to ONE customer.

        Deliberately the only user listing a customer-scoped caller ever reaches. The
        tenant-wide endpoint returns every bank's users in a single page — verified
        against production, where one call returned Canara Bank and Bank of India
        accounts together — so routing a branch user through it would enumerate other
        banks' staff. ThingsBoard also rejects a cross-customer id with 403, but the
        id here comes from /api/auth/user, never from the question.
        """
        require_uuid(customer_id, "customer_id")
        return await fetch_all_pages(
            self._get, f"/api/customer/{customer_id}/users", page_size
        )

    async def tenant_users(self, page_size: int = 100) -> Any:
        """Every user in the tenant. ThingsBoard returns 403 unless the CALLER really
        is a tenant admin, which is what makes it safe to call on their behalf.

        The path is /api/user/users, NOT /api/tenant/users. ThingsBoard routes the
        latter as /api/tenant/{tenantId}/users and tries to parse "users" as the id,
        so every call died with 400 "Invalid UUID string: users" — 72 of the 769 FAQ
        questions returned HTTP 500. docs/API-TB.md documented the wrong path (the
        same way it did for /api/alarms/DEVICE/{id}) and the unit test stubs this
        method, so nothing on the green path ever issued the real request.
        """
        return await fetch_all_pages(self._get, "/api/user/users", page_size)

    async def all_alarms(
        self,
        search_status: str = "ANY",
        page_size: int = 100,
        max_pages: int = _MAX_ALARM_PAGES,
    ) -> Any:
        """Every alarm the CALLER may see, in one paginated read.

        ThingsBoard scopes /api/alarms to the caller's own permissions, so this is
        both correct and vastly cheaper than asking per device: a Bank of India
        head-office token covers ~100 devices, which was ~100 HTTP calls to answer
        one question. The caller still intersects the result with its own narrower
        scope, since regional scoping can be tighter than ThingsBoard's ACL.
        """

        async def get(path: str, params: dict[str, Any]) -> Any:
            return await self._get(
                path,
                {
                    **params,
                    "searchStatus": search_status,
                    "sortProperty": "createdTime",
                    "sortOrder": "DESC",
                },
            )

        return await fetch_all_pages(get, "/api/alarms", page_size, max_pages=max_pages)

    async def alarms(self, device_id: str, page_size: int = 100) -> Any:
        """Alarm history constrained by both TB ACL and the requested device."""
        require_uuid(device_id, "device_id")

        async def get(path: str, params: dict[str, Any]) -> Any:
            return await self._get(
                path,
                {
                    **params,
                    "searchStatus": "ANY",
                    "sortProperty": "createdTime",
                    "sortOrder": "DESC",
                },
            )

        return await fetch_all_pages(
            get,
            f"/api/alarm/DEVICE/{device_id}",
            page_size,
            max_pages=_MAX_ALARM_PAGES,
        )
