"""RabbitMQ event consumer — the diagram's Consumer Profile.

Per message: idempotency check (Redis SETNX, 24h — Java IdempotencyService) →
persist DeviceEvent (Postgres, unique constraint as the authoritative dedup) →
fold the payload into the customer's fleet snapshot hash so Redis stays the LIVE
source of truth for chat, per event, not per 60s sync cycle.

SECURITY: an event only touches the fleet snapshot if its device_id exists in the
claimed customer's OWN hierarchy — a mislabeled or hostile event cannot poison
another customer's state (same rule as replay).
"""

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

import aio_pika
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import HierarchyNode
from app.ingest.parse import EventParse
from app.ingest.publisher import (
    CATCH_ALL_QUEUE,
    QUEUE_ARGS,
    declare_topology,
    routing_key_for,
)
from app.ingest.write import write_event
from app.tasks.live_sync import merge_device_state
from app.tasks.replay import fold_payload

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60  # Java: Redis SETNX, 24h TTL
_KNOWN_DEVICE_CACHE_TTL = 60.0

# (customer, device) -> (is_known, expires_at). Process-local; entries expire so a
# freshly imported device is picked up within a minute.
_known_cache: dict[tuple[str, str], tuple[bool, float]] = {}


def _seen_key(tenant_id: str, event_id: str) -> str:
    return f"evt:seen:v1:{tenant_id}:{event_id}"


async def already_processed(redis: "Redis", event: EventParse) -> bool:
    """True if this (tenant, event_id) was fully processed in the last 24h.

    Best-effort fast path: on Redis failure we say "not seen" and let the DB unique
    constraint do the authoritative dedup.
    """
    try:
        return bool(await redis.get(_seen_key(event.tenant_id, event.event_id)))
    except Exception:
        logger.warning("idempotency read failed; falling through to DB dedup", exc_info=True)
        return False


async def mark_processed(redis: "Redis", event: EventParse) -> None:
    """Marked AFTER successful processing, so a crash mid-event lets the redelivery
    re-run (the DB constraint and field-level HSET make the re-run harmless)."""
    try:
        await redis.set(
            _seen_key(event.tenant_id, event.event_id), "1", ex=IDEMPOTENCY_TTL_SECONDS
        )
    except Exception:
        logger.warning("idempotency write failed", exc_info=True)


async def _device_in_customer_hierarchy(
    session: AsyncSession, customer: str, device_id: str
) -> bool:
    cache_key = (customer, device_id)
    cached = _known_cache.get(cache_key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    try:
        device_uuid = UUID(device_id)
    except (ValueError, AttributeError, TypeError):
        _known_cache[cache_key] = (False, now + _KNOWN_DEVICE_CACHE_TTL)
        return False  # not a UUID -> cannot be a ThingsBoard device in the hierarchy
    row = (
        await session.execute(
            select(HierarchyNode.node_id).where(
                HierarchyNode.customer_id == customer,
                HierarchyNode.tb_device_id == device_uuid,
                HierarchyNode.is_leaf.is_(True),
            )
        )
    ).first()
    known = row is not None
    _known_cache[cache_key] = (known, now + _KNOWN_DEVICE_CACHE_TTL)
    return known


async def process_event(
    sessions: async_sessionmaker[AsyncSession], redis: "Redis", event: EventParse
) -> str:
    """Returns 'duplicate' | 'stored' | 'stored_no_fold' (for observability/tests)."""
    if await already_processed(redis, event):
        return "duplicate"
    async with sessions() as session:
        await write_event(session, event)
        folded = False
        if event.customer_id and await _device_in_customer_hierarchy(
            session, event.customer_id, event.device_id
        ):
            updates: dict[str, object] = {"device_id": event.device_id}
            fold_payload(updates, event.payload)
            await merge_device_state(redis, event.customer_id, event.device_id, updates)
            folded = True
    await mark_processed(redis, event)
    return "stored" if folded else "stored_no_fold"


async def consume_events(
    url: str,
    sessions: async_sessionmaker[AsyncSession],
    redis: "Redis",
    customer: str | None = None,
    default_tenant: str = "",
) -> None:
    """Consume from the topic exchange. Default worker takes the catch-all queue
    (every customer); pass a customer prefix to run a dedicated worker that binds
    its own queue to just that customer's routing key (diagram: per-customer
    queues + bindings on a topic exchange).

    Deployment note: a topic exchange delivers to EVERY matching bound queue, and the
    catch-all binding (customer.#) matches all customers — so run the catch-all worker
    OR per-customer workers, not both, unless double processing (idempotent, but
    wasted) is acceptable."""
    connection = await aio_pika.connect_robust(url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=20)
    exchange = await declare_topology(channel)
    if customer:
        queue = await channel.declare_queue(
            f"{CATCH_ALL_QUEUE}.{customer}", durable=True, arguments=QUEUE_ARGS
        )
        await queue.bind(exchange, routing_key=routing_key_for(customer))
    else:
        queue = await channel.declare_queue(CATCH_ALL_QUEUE, durable=True, arguments=QUEUE_ARGS)
    logger.info("consuming %s", queue.name)
    async with queue.iterator() as messages:
        async for message in messages:
            async with message.process(requeue=False):
                event = EventParse.from_payload(json.loads(message.body), default_tenant)
                await process_event(sessions, redis, event)


def main() -> None:
    """Standalone worker: python -m app.ingest.consumer [--customer BOI]"""
    import argparse

    from app.cache.redis import create_redis
    from app.config import get_settings
    from app.db.session import build_session_factory

    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", default=None, help="dedicated per-customer worker")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    sessions = build_session_factory(settings)

    async def _run() -> None:
        redis = await create_redis(settings.redis_url)
        try:
            await consume_events(settings.rabbitmq_url, sessions, redis,
                                 customer=args.customer,
                                 default_tenant=settings.webhook_default_tenant_id)
        finally:
            await redis.aclose()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
