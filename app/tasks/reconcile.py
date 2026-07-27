"""Drift reconciliation — port of Java ReconciliationService.

Java compared DB-truth online/offline counters against Redis counters nightly and
auto-repaired via replay. v2 has no counter keys — the fleet snapshot IS per-device —
so drift here means: a device has recent DeviceEvent history in Postgres but no
snapshot in Redis (the snapshot store lost data or was flushed). Repair is the same
as Java: replay the last 30 days for that customer.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DeviceEvent, HierarchyNode
from app.tasks.live_sync import load_fleet_states
from app.tasks.replay import ReplayInProgressError, replay_customer

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

REPAIR_WINDOW_DAYS = 30  # Java: auto-repair replays the last 30 days
EVENT_LOOKBACK_DAYS = 30  # only devices with events this recent are expected in Redis


@dataclass(frozen=True)
class ReconcileResult:
    customer: str
    total_devices: int
    devices_with_events: int
    snapshot_present: int
    missing: int
    repaired: bool


def detect_missing(
    device_ids: list[str], event_device_ids: set[str], snapshot_device_ids: set[str]
) -> set[str]:
    """Devices that provably have data (DB events) but no fleet snapshot."""
    known = set(device_ids)
    return (event_device_ids & known) - snapshot_device_ids


async def reconcile_customer(
    session: AsyncSession,
    redis: "Redis",
    customer: str,
    auto_repair: bool = True,
    repair_fn: Callable[[AsyncSession, "Redis", str, datetime, datetime], Awaitable[object]]
    | None = None,
) -> ReconcileResult:
    now = datetime.now(UTC)
    leaves = list(
        (
            await session.execute(
                select(HierarchyNode).where(
                    HierarchyNode.customer_id == customer, HierarchyNode.is_leaf.is_(True)
                )
            )
        ).scalars()
    )
    device_ids = [str(n.tb_device_id) for n in leaves if n.tb_device_id]
    event_device_ids = set(
        (
            await session.execute(
                select(DeviceEvent.device_id)
                .where(
                    DeviceEvent.customer_id == customer,
                    DeviceEvent.time >= now - timedelta(days=EVENT_LOOKBACK_DAYS),
                )
                .distinct()
            )
        ).scalars()
    )
    states = await load_fleet_states(redis, customer, device_ids)
    missing = detect_missing(device_ids, event_device_ids, set(states))

    repaired = False
    if missing:
        logger.warning(
            "[RECONCILE] drift for %s: %d device(s) have DB events but no snapshot",
            customer,
            len(missing),
        )
        if auto_repair:
            repair = repair_fn or replay_customer
            try:
                await repair(session, redis, customer, now - timedelta(days=REPAIR_WINDOW_DAYS), now)
                repaired = True
            except ReplayInProgressError:
                logger.info("[RECONCILE] repair skipped for %s - rebuild already running", customer)
    else:
        logger.info("[RECONCILE] %s consistent (%d devices)", customer, len(device_ids))

    return ReconcileResult(
        customer=customer,
        total_devices=len(device_ids),
        devices_with_events=len(event_device_ids),
        snapshot_present=len(states),
        missing=len(missing),
        repaired=repaired,
    )


async def reconcile_all(
    session_factory: async_sessionmaker[AsyncSession], redis: "Redis", auto_repair: bool = True
) -> None:
    async with session_factory() as session:
        customers = list(
            (await session.execute(select(HierarchyNode.customer_id).distinct())).scalars()
        )
        for customer in customers:
            try:
                await reconcile_customer(session, redis, customer, auto_repair)
            except Exception:
                logger.exception("[RECONCILE] failed for customer %s", customer)
