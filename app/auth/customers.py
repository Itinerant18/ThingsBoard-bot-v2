"""Customer prefix resolution from ThingsBoard customer ID/title."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer

if TYPE_CHECKING:
    from app.config import Settings


@dataclass(frozen=True)
class CustomerResolutionResult:
    prefix: str | None
    from_cache: bool = False


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_title_mappings(mappings_str: str) -> dict[str, str]:
    """Parse 'Title=PREFIX,Title2=PREFIX2' into dict."""
    result = {}
    for pair in _split_csv(mappings_str):
        if "=" in pair:
            title, prefix = pair.split("=", 1)
            result[title.strip().upper()] = prefix.strip().upper()
    return result


def _normalize_title(title: str) -> str:
    """Normalize title for matching: uppercase, collapse whitespace/punct."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", title.upper()).split())


def _find_prefix_by_title(
    title: str, known_prefixes: set[str], title_mappings: dict[str, str]
) -> str | None:
    """Find unique prefix match from title using mappings and word/prefix matching."""
    normalized = _normalize_title(title)

    # 1. Check explicit title mappings
    if normalized in title_mappings:
        mapped = title_mappings[normalized]
        if mapped in known_prefixes:
            return mapped
        return None

    # 2. Find all known prefixes that match as word or prefix in normalized title
    candidates = set()
    words = normalized.split()
    for prefix in known_prefixes:
        if prefix in words or any(word.startswith(prefix) for word in words):
            candidates.add(prefix)

    if len(candidates) == 1:
        return candidates.pop()
    return None


async def resolve_customer_prefix(
    session: AsyncSession | None,
    tb_customer_id: str | None,
    tb_customer_title: str | None,
    settings: "Settings",
    redis: Redis | None = None,
) -> str | None:
    """
    Resolve customer prefix from tb_customer_id (authoritative) or title matching.

    Priority:
    1. Redis cache by tb_customer_id (5 min TTL)
    2. DB lookup by tb_customer_id (authoritative)
    3. Title matching using known prefixes + settings overrides
    4. None -> caller handles fail-closed via strict_customer_mapping
    """
    known_prefixes = set(settings.prefixes)
    title_mappings = _parse_title_mappings(settings.customers_title_mappings)

    # If we have tb_customer_id, try cache first
    if tb_customer_id:
        cache_key = f"customer:prefix:{tb_customer_id}"
        if redis:
            cached = await redis.get(cache_key)
            if cached:
                return cached.decode() if isinstance(cached, bytes) else cached

        # Try DB lookup
        if session:
            result = await session.execute(
                select(Customer.prefix).where(Customer.tb_customer_id == tb_customer_id)
            )
            db_prefix = result.scalar_one_or_none()
            if db_prefix:
                if redis:
                    await redis.setex(cache_key, 300, db_prefix)  # 5 min TTL
                return db_prefix

    # Fallback: title matching
    if tb_customer_title:
        return _find_prefix_by_title(tb_customer_title, known_prefixes, title_mappings)

    return None