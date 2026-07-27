"""The set of devices ThingsBoard authorizes for a specific caller.

Why this exists: v2 derives scope from the customer PREFIX (e.g. "BOI"), but a
prefix aggregates several ThingsBoard customers. Measured against production on
2026-07-27, a real CUSTOMER_USER token for BOI-MALDATOWN was authorized by
ThingsBoard for 100 devices while v2's prefix scope covered 104 — so six devices
(BOI-BAS, BOI-DX7, BOI-BAHALDA, BOI-LOHARDAGA-CC, BOI-DX5, BOI-R-BAZAR) were
answerable by a user ThingsBoard says cannot see them. No forgery involved; the
token was genuine.

The set returned here is a CEILING. Local hierarchy/region scope may narrow it and
must never widen it, so a stale mirror or an unresolvable region cannot leak.

Identity comes from GET /api/auth/user, never from JWT claims. That endpoint's
`authority` is ThingsBoard's verdict; the token's `scopes` claim is whatever the
sender wrote there. Reading the claim instead is precisely the Java bug that let a
forged `TENANT_ADMIN` scope unlock every customer's fleet.
"""

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from app.clients.thingsboard import UserAwareThingsBoardClient
from app.config import Settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "tbacl:v1:"


class PermissionCheckUnavailable(Exception):
    """ThingsBoard could not confirm what the caller may see.

    Raised instead of returning an empty or unfiltered set so callers must decide
    explicitly. Every caller fails CLOSED: an unreachable ThingsBoard means we
    refuse to answer, never that we answer from the unchecked local mirror.
    """


def _cache_key(token: str) -> str:
    """Hash the token — never store the credential itself as a key.

    (The Java build used the raw JWT as its chat-memory session id; same class of
    mistake, and it also meant history was lost on every token refresh.)
    """
    return _CACHE_PREFIX + hashlib.sha256(token.encode()).hexdigest()[:32]


def _device_ids(page: Any) -> set[str]:
    rows = page.get("data") if isinstance(page, dict) else None
    ids: set[str] = set()
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("id")
        value = raw.get("id") if isinstance(raw, dict) else raw
        if value:
            ids.add(str(value))
    return ids


async def authorized_device_ids(
    settings: Settings, user_token: str, redis: "Redis"
) -> frozenset[str]:
    """Device ids ThingsBoard authorizes for the holder of `user_token`.

    Raises PermissionCheckUnavailable if that cannot be established.
    """
    if not user_token:
        raise PermissionCheckUnavailable("no caller token")

    key = _cache_key(user_token)
    try:
        cached = await redis.get(key)
    except Exception:
        # A cache outage must not widen scope, but it also should not deny service:
        # fall through and ask ThingsBoard directly.
        logger.warning("[TB-ACL] cache read failed; querying ThingsBoard", exc_info=True)
        cached = None
    if cached:
        raw = cached.decode() if isinstance(cached, bytes) else str(cached)
        try:
            return frozenset(json.loads(raw))
        except (ValueError, TypeError):
            logger.warning("[TB-ACL] discarding unreadable cache entry")

    client = UserAwareThingsBoardClient(settings, user_token)
    try:
        user = await client.current_user()
        if not isinstance(user, dict):
            raise PermissionCheckUnavailable("unexpected /api/auth/user response")

        authority = str(user.get("authority") or "")
        raw_customer = user.get("customerId")
        customer_id = raw_customer.get("id") if isinstance(raw_customer, dict) else raw_customer

        if authority == "TENANT_ADMIN":
            page = await client.tenant_devices()
        elif customer_id:
            page = await client.devices(str(customer_id))
        else:
            # Authenticated but assigned to no customer: authorized for nothing.
            page = {"data": []}

        ids = _device_ids(page)
    except PermissionCheckUnavailable:
        raise
    except Exception as exc:
        # Includes 401 (token expired mid-session) and any transport failure.
        logger.warning("[TB-ACL] could not resolve caller permissions", exc_info=True)
        raise PermissionCheckUnavailable(str(exc)) from exc
    finally:
        await client.close()

    try:
        await redis.set(key, json.dumps(sorted(ids)), ex=settings.tb_acl_cache_seconds)
    except Exception:
        logger.warning("[TB-ACL] cache write failed", exc_info=True)

    logger.info("[TB-ACL] authority=%s authorized_devices=%d", authority, len(ids))
    return frozenset(ids)
