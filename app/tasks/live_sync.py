"""Scheduled live sync — port of Java ScheduledLiveSyncService.

Periodically pulls every hierarchy device's latest telemetry + attributes from
ThingsBoard (service token) and writes a per-device raw-state snapshot into Redis.
Fleet-level questions (global overview online/offline counts) read this snapshot;
single-device questions still live-fetch per request with the caller's token.

ponytail (same as Java): naive per-device loop — 1 telemetry + 3 attribute calls per
device per cycle. Fine for a ~150-device fleet on a 60s interval; switch to the TB
bulk entity-query API if fleet size or interval makes call volume matter.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import HierarchyNode
from app.ingest.telemetry import changed_fields, write_telemetry

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_ATTRIBUTE_SCOPES = ("SERVER_SCOPE", "SHARED_SCOPE", "CLIENT_SCOPE")
_PREFIX = "fleet:v1"
# Snapshot outlives several missed cycles but does not linger forever if sync stops.
SNAPSHOT_TTL_SECONDS = 15 * 60
_LOCK_TTL_SECONDS = 5 * 60


def _state_key(customer: str, device_id: str) -> str:
    return f"{_PREFIX}:{customer}:{device_id}"


def _lock_key(customer: str) -> str:
    return f"{_PREFIX}:lock:{customer}"


async def try_rebuild_lock(redis: "Redis", customer: str) -> bool:
    """Java's replay lock: one snapshot rebuild (live sync OR replay) per customer at
    a time. EX bounds the hold if a process dies mid-rebuild."""
    return bool(await redis.set(_lock_key(customer), "1", nx=True, ex=_LOCK_TTL_SECONDS))


async def release_rebuild_lock(redis: "Redis", customer: str) -> None:
    await redis.delete(_lock_key(customer))


def _encode(value: Any) -> str:
    """Hash field values are strings; non-strings become JSON so list/dict payloads
    (camera arrays etc.) stay parseable by the normalization ladders."""
    return value if isinstance(value, str) else json.dumps(value, default=str)


async def store_device_state(
    redis: "Redis", customer: str, device_id: str, fields: dict[str, Any]
) -> None:
    """REPLACE one device's raw state (full rebuild path: live sync / replay).

    The snapshot is a Redis HASH (Java: HSET deviceState) so the event consumer can
    merge single fields atomically without read-modify-write races against rebuilds.
    """
    key = _state_key(customer, device_id)
    mapping = {str(k): _encode(v) for k, v in fields.items()}
    if not mapping:
        return
    await redis.delete(key)
    await redis.hset(key, mapping=cast("dict[Any, Any]", mapping))
    await redis.expire(key, SNAPSHOT_TTL_SECONDS)


async def merge_device_state(
    redis: "Redis", customer: str, device_id: str, updates: dict[str, Any]
) -> None:
    """Per-event field merge (event consumer path) — HSET is per-field atomic, so a
    concurrent rebuild can interleave without corrupting unrelated fields."""
    mapping = {str(k): _encode(v) for k, v in updates.items()}
    if not mapping:
        return
    key = _state_key(customer, device_id)
    await redis.hset(key, mapping=cast("dict[Any, Any]", mapping))
    await redis.expire(key, SNAPSHOT_TTL_SECONDS)


@dataclass(frozen=True)
class DeviceRef:
    device_id: str
    node_id: str
    display_name: str


class _TbClient(Protocol):
    async def telemetry(self, device_id: str, keys: str | None = ...) -> Any: ...
    async def attributes(self, device_id: str, scope: str) -> Any: ...


async def fetch_device_fields(tb: _TbClient, device_id: str) -> dict[str, Any]:
    """Latest telemetry + all attribute scopes, flattened; telemetry wins on collision."""
    fields: dict[str, Any] = {}
    for scope in _ATTRIBUTE_SCOPES:
        attrs = await tb.attributes(device_id, scope)
        if isinstance(attrs, list):
            for item in attrs:
                if isinstance(item, dict) and "key" in item:
                    fields[str(item["key"])] = item.get("value")
    series = await tb.telemetry(device_id)
    if isinstance(series, dict):
        for key, points in series.items():
            if isinstance(points, list) and points and isinstance(points[0], dict):
                value = points[0].get("value")
                # Never let a null reading erase a populated attribute. This path is
                # safe today only by luck — it requests no explicit `keys`, so
                # ThingsBoard omits empty ones — but adding a keys list here would
                # silently blank every nested subsystem container.
                if value is None and fields.get(str(key)) is not None:
                    continue
                fields[str(key)] = value
    return fields


async def _read_device_state(redis: "Redis", customer: str, device_id: str) -> dict[str, Any]:
    """Current Redis hash for a device — the baseline for change detection.

    Reusing the cache as the baseline means no extra state to keep: whatever the last
    cycle stored IS the last observation. A cache miss returns {}, which makes every
    key look new and writes a full snapshot — correct, if occasionally verbose.
    """
    try:
        raw = await redis.hgetall(_state_key(customer, device_id))
    except Exception:
        logger.warning("[LIVE-SYNC] baseline read failed for %s", device_id, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {_text(k): _text(v) for k, v in raw.items()}


async def sync_customer(
    redis: "Redis",
    tb: _TbClient,
    customer: str,
    devices: Sequence[DeviceRef],
    session_factory: "async_sessionmaker[AsyncSession] | None" = None,
    write_mode: str = "changed",
) -> int:
    """Fetch each device, cache it in Redis and append its history to Tiger.

    A per-customer Redis lock (Java: replay lock) prevents two processes rebuilding
    the same customer at once; a running cycle is skipped, not queued. A device whose
    fetch fails keeps its previous snapshot (TTL not refreshed) rather than being wiped.

    History is written BEFORE the cache is replaced, because the cached hash is the
    change-detection baseline — overwriting it first would make every key look
    unchanged and silently persist nothing.

    A database failure must not cost the cache update: the snapshot is what answers
    live questions, so persistence errors are logged and the cycle continues.
    """
    if not await try_rebuild_lock(redis, customer):
        logger.info("[LIVE-SYNC] rebuild already in progress for %s - skipping cycle", customer)
        return 0
    synced = 0
    persisted = 0
    try:
        for device in devices:
            try:
                fields = await fetch_device_fields(tb, device.device_id)
            except Exception:
                logger.warning(
                    "[LIVE-SYNC] fetch failed for %s (%s)",
                    device.display_name,
                    device.device_id,
                    exc_info=True,
                )
                continue
            if not fields:
                continue
            fields["device_id"] = device.device_id
            fields["branch_name"] = device.display_name
            fields["node_id"] = device.node_id

            if session_factory is not None:
                try:
                    baseline = (
                        None
                        if write_mode == "all"
                        else await _read_device_state(redis, customer, device.device_id)
                    )
                    to_write = changed_fields(fields, baseline)
                    if to_write:
                        async with session_factory() as session:
                            persisted += await write_telemetry(
                                session,
                                device.device_id,
                                to_write,
                                customer_id=customer,
                            )
                except Exception:
                    logger.warning(
                        "[LIVE-SYNC] telemetry persist failed for %s",
                        device.device_id,
                        exc_info=True,
                    )

            await store_device_state(redis, customer, device.device_id, fields)
            synced += 1
        logger.info(
            "[LIVE-SYNC] synced %d/%d devices for %s (%d telemetry rows)",
            synced,
            len(devices),
            customer,
            persisted,
        )
        return synced
    finally:
        await release_rebuild_lock(redis, customer)


async def sync_all_customers(
    session_factory: async_sessionmaker[AsyncSession],
    redis: "Redis",
    tb: _TbClient,
    write_mode: str = "changed",
) -> None:
    """DB glue: enumerate customers + leaf devices, then sync each customer."""
    async with session_factory() as session:
        result = await session.execute(
            select(
                HierarchyNode.customer_id,
                HierarchyNode.tb_device_id,
                HierarchyNode.node_id,
                HierarchyNode.display_name,
            ).where(HierarchyNode.is_leaf.is_(True), HierarchyNode.tb_device_id.is_not(None))
        )
        rows = result.all()
    by_customer: dict[str, list[DeviceRef]] = {}
    for customer_id, tb_device_id, node_id, display_name in rows:
        by_customer.setdefault(customer_id, []).append(
            DeviceRef(str(tb_device_id), node_id, display_name)
        )
    for customer, devices in by_customer.items():
        try:
            await sync_customer(
                redis, tb, customer, devices, session_factory=session_factory, write_mode=write_mode
            )
        except Exception:
            logger.exception("[LIVE-SYNC] sync failed for customer %s", customer)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def load_fleet_states(
    redis: "Redis", customer: str, device_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Read synced raw states for the given devices. Fail-open: {} on any Redis error.

    SECURITY: callers pass their SCOPED device id list; keys are also namespaced by
    customer, so neither a cross-customer nor a cross-region read is possible here.
    """
    if not device_ids:
        return {}
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for device_id in device_ids:
                pipe.hgetall(_state_key(customer, device_id))
            values = await pipe.execute()
    except Exception:
        logger.warning("[LIVE-SYNC] fleet state read failed", exc_info=True)
        return {}
    states: dict[str, dict[str, Any]] = {}
    for device_id, raw in zip(device_ids, values or [], strict=False):
        if not isinstance(raw, dict) or not raw:
            continue
        states[device_id] = {_text(k): _text(v) for k, v in raw.items()}
    return states
