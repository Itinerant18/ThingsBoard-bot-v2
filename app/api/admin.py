import hmac
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import Customer, HierarchyNode
from app.hierarchy.parser import ParsedNode, parse_device_path
from app.hierarchy.prefix import derive_prefix
from app.hierarchy.store import rebuild_ancestor_paths, upsert_nodes
from app.tasks.live_sync import sync_all_customers
from app.tasks.replay import ReplayInProgressError, replay

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def check_admin(request: Request, token: str | None) -> None:
    settings = request.app.state.settings
    if settings.require_admin_token and not settings.admin_token:
        raise HTTPException(503, "Admin token required but not configured")
    if settings.admin_token and not hmac.compare_digest(settings.admin_token, token or ""):
        raise HTTPException(403, "Invalid admin token")


class NodePayload(BaseModel):
    node_id: str
    customer_id: str
    parent_id: str | None = None
    node_type: str
    node_level: int
    display_name: str
    is_leaf: bool = False
    tb_device_id: UUID | None = None


@router.post("/import")
async def import_hierarchy(
    request: Request,
    payload: list[dict[str, object]],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    check_admin(request, x_admin_token)
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        prefixes = set(settings.prefixes) | {
            value for value in (await session.execute(select(Customer.prefix))).scalars() if value
        }
        nodes: list[ParsedNode] = []
        touched: set[str] = set()
        skipped = 0
        for device in payload:
            name = str(device.get("name") or "")
            telemetry = (
                cast(dict[str, Any], device.get("telemetry"))
                if isinstance(device.get("telemetry"), dict)
                else {}
            )
            attributes = (
                cast(dict[str, Any], device.get("serverAttributes"))
                if isinstance(device.get("serverAttributes"), dict)
                else {}
            )
            full_path = str(telemetry.get("full_path") or attributes.get("full_path") or "")
            prefix = derive_prefix(name, full_path, prefixes)
            if not prefix:
                skipped += 1
                continue
            nodes.extend(parse_device_path(prefix, name, str(device.get("id") or ""), full_path))
            touched.add(prefix)
        await upsert_nodes(session, nodes)
        for customer in touched:
            await rebuild_ancestor_paths(session, customer)
        await session.commit()
    return {
        "devices": len(payload),
        "nodes": len({node.node_id for node in nodes}),
        "customers": sorted(touched),
        "skipped": skipped,
    }


@router.get("/hierarchy")
async def hierarchy(
    request: Request, customer_id: str, x_admin_token: str | None = Header(default=None)
) -> list[dict[str, object]]:
    check_admin(request, x_admin_token)
    async with request.app.state.session_factory() as session:
        nodes = list(
            (
                await session.execute(
                    select(HierarchyNode).where(HierarchyNode.customer_id == customer_id)
                )
            ).scalars()
        )
    by_parent: dict[str | None, list[HierarchyNode]] = defaultdict(list)
    for node in nodes:
        by_parent[node.parent_id].append(node)

    def render(node: HierarchyNode) -> dict[str, object]:
        return {
            "node_id": node.node_id,
            "node_type": node.node_type,
            "display_name": node.display_name,
            "is_leaf": node.is_leaf,
            "children": [render(child) for child in by_parent[node.node_id]],
        }

    return [render(node) for node in by_parent[None]]


@router.post("/node")
async def upsert_node(
    request: Request, payload: NodePayload, x_admin_token: str | None = Header(default=None)
) -> dict[str, int]:
    check_admin(request, x_admin_token)
    async with request.app.state.session_factory() as session:
        count = await upsert_nodes(session, [ParsedNode(**payload.model_dump())])
        await rebuild_ancestor_paths(session, payload.customer_id)
        await session.commit()
    return {"nodes": count}


class ReplayRequest(BaseModel):
    customer_id: str  # a customer prefix, or "ALL"
    start_time: datetime | None = None  # default: 7 days ago (Java parity)
    end_time: datetime | None = None  # default: now


@router.post("/replay")
async def replay_events(
    request: Request, payload: ReplayRequest, x_admin_token: str | None = Header(default=None)
) -> dict[str, object]:
    """Rebuild fleet snapshots from stored DeviceEvent history (port of Java /admin/replay)."""
    check_admin(request, x_admin_token)
    start = payload.start_time or datetime.now(UTC) - timedelta(days=7)
    end = payload.end_time or datetime.now(UTC)
    try:
        results = await replay(
            request.app.state.session_factory,
            request.app.state.redis,
            payload.customer_id,
            start,
            end,
        )
    except ReplayInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "SUCCESS",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "results": [
            {
                "customer": r.customer,
                "events": r.events,
                "devices": r.devices,
                "skipped_unknown_devices": r.skipped_unknown_devices,
            }
            for r in results
        ],
    }


@router.post("/init")
async def init_fleet_snapshot(
    request: Request, x_admin_token: str | None = Header(default=None)
) -> dict[str, object]:
    """Bootstrap the fleet snapshot NOW by running one live-sync cycle synchronously —
    a fresh deployment gets fleet answers without waiting for the scheduler."""
    check_admin(request, x_admin_token)
    await sync_all_customers(
        request.app.state.session_factory, request.app.state.redis, request.app.state.tb
    )
    return {"status": "SUCCESS", "message": "Fleet snapshot initialized from ThingsBoard."}
