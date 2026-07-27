import json
from typing import Any

from redis.asyncio import Redis, from_url


async def create_redis(url: str) -> Redis:
    client = from_url(url, decode_responses=True)
    await client.ping()
    return client


async def set_snapshot(
    redis: Redis, tenant_id: str, device_id: str, value: dict[str, Any], ttl: int = 90
) -> None:
    await redis.set(f"snapshot:{tenant_id}:{device_id}", json.dumps(value), ex=ttl)


async def get_snapshot(redis: Redis, tenant_id: str, device_id: str) -> dict[str, Any] | None:
    value = await redis.get(f"snapshot:{tenant_id}:{device_id}")
    return json.loads(value) if value else None


async def remember(
    redis: Redis, tenant_id: str, conversation_id: str, item: dict[str, Any], ttl: int = 86400
) -> None:
    key = f"memory:{tenant_id}:{conversation_id}"
    await redis.rpush(key, json.dumps(item))
    await redis.ltrim(key, -20, -1)
    await redis.expire(key, ttl)
