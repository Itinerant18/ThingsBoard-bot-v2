import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.jwt import TenantContext
from app.clients.thingsboard import UserAwareThingsBoardClient, require_uuid
from app.deps import current_tenant, scoped_branches, user_tb_client
from app.hierarchy.scope import ScopedBranches
from app.query.charts import DEFAULT_WINDOW_HOURS, MAX_WINDOW_HOURS, chart_from_history

logger = logging.getLogger(__name__)

router = APIRouter(tags=["data"])


def enforce_device_scope(device_id: str, branches: ScopedBranches) -> None:
    try:
        require_uuid(device_id, "device_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if device_id not in branches.tb_device_ids:
        raise HTTPException(status_code=403, detail="Device not in caller's regional scope")


def _scope_device_page(page: object, branches: ScopedBranches) -> list[object]:
    """Filter a TB device page down to the caller's regional scope.

    SECURITY: TB's ACL returns the customer's WHOLE inventory — the regional
    sub-scope exists only in our hierarchy, so it must be applied here.
    """
    items = page.get("data", page) if isinstance(page, dict) else page
    if not isinstance(items, list):
        return []
    allowed = set(branches.tb_device_ids)
    scoped: list[object] = []
    for item in items:
        if isinstance(item, dict):
            raw = item.get("id")
            device_id = raw.get("id") if isinstance(raw, dict) else raw
            if str(device_id) in allowed:
                scoped.append(item)
    return scoped


@router.get("/api/v1/data")
async def data(
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    branches: Annotated[ScopedBranches, Depends(scoped_branches)],
    user_tb: Annotated[UserAwareThingsBoardClient, Depends(user_tb_client)],
) -> object:
    if not tenant.customer_id:
        return {"devices": []}
    page = await user_tb.devices(tenant.customer_id)
    return {"devices": _scope_device_page(page, branches)}


@router.get("/all-devices")
async def all_devices(
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    branches: Annotated[ScopedBranches, Depends(scoped_branches)],
    user_tb: Annotated[UserAwareThingsBoardClient, Depends(user_tb_client)],
) -> object:
    if not tenant.customer_id:
        return {"data": []}
    page = await user_tb.devices(tenant.customer_id)
    return {"data": _scope_device_page(page, branches)}


@router.get("/device/{device_id}/telemetry")
async def telemetry(
    device_id: str,
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    branches: Annotated[ScopedBranches, Depends(scoped_branches)],
    user_tb: Annotated[UserAwareThingsBoardClient, Depends(user_tb_client)],
) -> object:
    enforce_device_scope(device_id, branches)
    return await user_tb.telemetry(device_id)


@router.get("/device/{device_id}/chart")
async def chart(
    device_id: str,
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    branches: Annotated[ScopedBranches, Depends(scoped_branches)],
    user_tb: Annotated[UserAwareThingsBoardClient, Depends(user_tb_client)],
    key: Annotated[str, Query(min_length=1, max_length=128)],
    hours: Annotated[int, Query(ge=1, le=MAX_WINDOW_HOURS)] = DEFAULT_WINDOW_HOURS,
) -> object:
    """Historical series for one telemetry key, Chart.js-shaped (Java ChartService)."""
    enforce_device_scope(device_id, branches)
    if "," in key:
        raise HTTPException(status_code=400, detail="One key per chart")
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - hours * 3_600_000
    try:
        history = await user_tb.telemetry(device_id, keys=key, start_ts=start_ts, end_ts=end_ts)
    except Exception:
        # Java parity: chart errors degrade to an empty series, not a 500.
        logger.warning("chart history fetch failed for %s/%s", device_id, key, exc_info=True)
        history = {}
    return chart_from_history(key, history)


@router.get("/device/{device_id}/attributes/client")
async def client_attributes(
    device_id: str,
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    branches: Annotated[ScopedBranches, Depends(scoped_branches)],
    user_tb: Annotated[UserAwareThingsBoardClient, Depends(user_tb_client)],
) -> object:
    enforce_device_scope(device_id, branches)
    return await user_tb.attributes(device_id, "CLIENT_SCOPE")


@router.get("/device/{device_id}/attributes/server")
async def server_attributes(
    device_id: str,
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    branches: Annotated[ScopedBranches, Depends(scoped_branches)],
    user_tb: Annotated[UserAwareThingsBoardClient, Depends(user_tb_client)],
) -> object:
    enforce_device_scope(device_id, branches)
    return await user_tb.attributes(device_id, "SERVER_SCOPE")