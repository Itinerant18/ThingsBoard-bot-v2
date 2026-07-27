"""DB event replay — port of Java ReplayService.

Rebuilds a customer's Redis fleet snapshot from the DeviceEvent history the webhook
ingest has stored, instead of calling ThingsBoard. Events are folded oldest-first, so
the final per-device state reflects the last value each field had in the window. The
output uses the SAME Redis layout and rebuild lock as the scheduled live sync, so
readers can't tell the difference and the two can never rebuild one customer at once.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DeviceEvent, HierarchyNode
from app.tasks.live_sync import release_rebuild_lock, store_device_state, try_rebuild_lock

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Webhook envelope fields — transport metadata, not device state.
_ENVELOPE_KEYS = frozenset(
    {"tenant_id", "device_id", "event_id", "customer_id", "ts", "event_type", "id"}
)
# Containers whose CONTENTS are device fields ({"data": {"cpu": 40}} -> cpu=40).
_VALUE_CONTAINERS = ("values", "data", "telemetry", "attributes")


class ReplayInProgressError(Exception):
    """Another rebuild (replay or live sync) holds this customer's lock (Java audit #12)."""


def fold_payload(state: dict[str, Any], payload: Mapping[str, Any]) -> None:
    """Merge one event's payload into a device's accumulated state (newer wins)."""
    for container in _VALUE_CONTAINERS:
        inner = payload.get(container)
        if isinstance(inner, Mapping):
            for key, value in inner.items():
                state[str(key)] = value
    for key, value in payload.items():
        if key in _ENVELOPE_KEYS or key in _VALUE_CONTAINERS:
            continue
        state[str(key)] = value


@dataclass(frozen=True)
class ReplayResult:
    customer: str
    events: int
    devices: int
    skipped_unknown_devices: int


async def replay_events(
    redis: "Redis",
    customer: str,
    leaves: Sequence[HierarchyNode],
    events: Sequence[DeviceEvent],
) -> ReplayResult:
    """Fold events into per-device states and store them under the rebuild lock.

    SECURITY: an event's device_id is client-supplied at webhook time; only devices
    present in the customer's OWN hierarchy are folded, so a mislabeled or hostile
    event can never poison another customer's fleet snapshot.
    """
    if not await try_rebuild_lock(redis, customer):
        raise ReplayInProgressError(f"Rebuild already in progress for customer {customer}")
    try:
        known: dict[str, HierarchyNode] = {
            str(n.tb_device_id): n for n in leaves if n.tb_device_id
        }
        states: dict[str, dict[str, Any]] = {}
        skipped = 0
        for event in events:  # caller orders by time ASC; later events overwrite
            node = known.get(event.device_id)
            if node is None:
                skipped += 1
                continue
            state = states.setdefault(
                event.device_id,
                {
                    "device_id": event.device_id,
                    "branch_name": node.display_name,
                    "node_id": node.node_id,
                },
            )
            if isinstance(event.payload, Mapping):
                fold_payload(state, event.payload)
        for device_id, fields in states.items():
            await store_device_state(redis, customer, device_id, fields)
        logger.info(
            "[REPLAY] customer=%s events=%d devices=%d skipped_unknown=%d",
            customer, len(events), len(states), skipped,
        )
        return ReplayResult(customer, len(events), len(states), skipped)
    finally:
        await release_rebuild_lock(redis, customer)


async def replay_customer(
    session: AsyncSession, redis: "Redis", customer: str, start: datetime, end: datetime
) -> ReplayResult:
    leaves = list(
        (
            await session.execute(
                select(HierarchyNode).where(
                    HierarchyNode.customer_id == customer, HierarchyNode.is_leaf.is_(True)
                )
            )
        ).scalars()
    )
    events = list(
        (
            await session.execute(
                select(DeviceEvent)
                .where(
                    DeviceEvent.customer_id == customer,
                    DeviceEvent.time >= start,
                    DeviceEvent.time <= end,
                )
                .order_by(DeviceEvent.time.asc())
            )
        ).scalars()
    )
    return await replay_events(redis, customer, leaves, events)


async def replay(
    session_factory: async_sessionmaker[AsyncSession],
    redis: "Redis",
    customer_id: str,
    start: datetime,
    end: datetime,
) -> list[ReplayResult]:
    """Replay one customer, or every customer when customer_id == "ALL" (Java parity).

    In ALL mode a per-customer failure is logged and the loop continues; a single
    named customer propagates its error (the API turns lock conflicts into 409).
    """
    async with session_factory() as session:
        if customer_id.upper() != "ALL":
            return [await replay_customer(session, redis, customer_id, start, end)]
        customers = list(
            (await session.execute(select(HierarchyNode.customer_id).distinct())).scalars()
        )
        results: list[ReplayResult] = []
        for customer in customers:
            try:
                results.append(await replay_customer(session, redis, customer, start, end))
            except Exception:
                logger.exception("[REPLAY] failed for customer %s", customer)
        return results
