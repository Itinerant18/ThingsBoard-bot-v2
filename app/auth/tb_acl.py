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

import base64
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx

from app.clients.thingsboard import UserAwareThingsBoardClient
from app.config import Settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "tbacl:v1:"
_IDENTITY_PREFIX = "tbacl:v1:id:"

# ThingsBoard's "no customer" sentinel. A TENANT_ADMIN carries it in customerId, and
# requesting it as a real customer id 404s.
_NULL_CUSTOMER = "13814000-1dd2-11b2-8080-808080808080"


class PermissionCheckUnavailable(Exception):
    """ThingsBoard could not confirm what the caller may see.

    Raised instead of returning an empty or unfiltered set so callers must decide
    explicitly. Every caller fails CLOSED: an unreachable ThingsBoard means we
    refuse to answer, never that we answer from the unchecked local mirror.
    """


class SessionExpired(PermissionCheckUnavailable):
    """ThingsBoard rejected the caller's token (401/403), or it is already expired.

    A subclass so every existing fail-closed path still applies, but callers can tell
    the two apart — and they must. "Retry in a moment" is correct for an unreachable
    ThingsBoard and actively misleading for a dead token: no amount of retrying will
    ever fix it, the user has to sign in again.

    Java classified this correctly at the HTTP layer (exchangeWithRetry retried only
    5xx and 429, never 401) but never carried the distinction up to the user.
    """


def is_expired(token: str) -> bool:
    """True if the token carries an `exp` already in the past.

    Advisory pre-flight, on an UNVERIFIED decode: it saves a pointless round-trip to
    ThingsBoard for a clearly-dead token and lets us name the real problem. A token
    that looks fine here is still validated by ThingsBoard — this can only reject
    early, never authorize.

    Ported from Java's JwtParserUtil.isExpired, which was written and documented but
    never actually called anywhere.
    """
    raw = token.removeprefix("Bearer ").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return False  # not a JWT shape; let ThingsBoard be the judge
    try:
        segment = parts[1]
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        exp = payload.get("exp")
    except (ValueError, TypeError, AttributeError):
        return False  # unparseable: fail open to ThingsBoard, which will reject it
    return isinstance(exp, int | float) and exp < time.time()


def _cache_key(token: str) -> str:
    """Hash the token — never store the credential itself as a key.

    (The Java build used the raw JWT as its chat-memory session id; same class of
    mistake, and it also meant history was lost on every token refresh.)
    """
    return _CACHE_PREFIX + hashlib.sha256(token.encode()).hexdigest()[:32]


class CallerIdentity(NamedTuple):
    """What ThingsBoard says the caller is. Never what the token claims to be."""

    authority: str
    customer_id: str | None

    @property
    def is_tenant_admin(self) -> bool:
        return self.authority == "TENANT_ADMIN"


def _identity_of(user: Any) -> CallerIdentity:
    if not isinstance(user, dict):
        raise PermissionCheckUnavailable("unexpected /api/auth/user response")
    raw_customer = user.get("customerId")
    customer_id = raw_customer.get("id") if isinstance(raw_customer, dict) else raw_customer
    # A TENANT_ADMIN's customerId is ThingsBoard's null-UUID sentinel, not a customer.
    if customer_id in (None, "", _NULL_CUSTOMER):
        customer_id = None
    return CallerIdentity(str(user.get("authority") or ""), customer_id and str(customer_id))


async def caller_identity(
    settings: Settings, user_token: str, redis: "Redis"
) -> CallerIdentity:
    """Resolve the caller's authority and customer from ThingsBoard.

    Separate from authorized_device_ids because the non-device reports (users, audit)
    need the same verdict, and asking twice in two ways is how two answers to the same
    security question drift apart.
    """
    if not user_token:
        raise PermissionCheckUnavailable("no caller token")
    if is_expired(user_token):
        raise SessionExpired("token expired")

    key = _IDENTITY_PREFIX + hashlib.sha256(user_token.encode()).hexdigest()[:32]
    try:
        cached = await redis.get(key)
    except Exception:
        logger.warning("[TB-ACL] identity cache read failed", exc_info=True)
        cached = None
    if cached:
        raw = cached.decode() if isinstance(cached, bytes) else str(cached)
        try:
            data = json.loads(raw)
            return CallerIdentity(str(data["authority"]), data["customer_id"])
        except (ValueError, TypeError, KeyError):
            logger.warning("[TB-ACL] discarding unreadable identity cache entry")

    client = UserAwareThingsBoardClient(settings, user_token)
    try:
        identity = _identity_of(await client.current_user())
    except PermissionCheckUnavailable:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise SessionExpired(f"thingsboard returned {exc.response.status_code}") from exc
        raise PermissionCheckUnavailable(str(exc)) from exc
    except Exception as exc:
        raise PermissionCheckUnavailable(str(exc)) from exc
    finally:
        await client.close()

    try:
        await redis.set(
            key,
            json.dumps({"authority": identity.authority, "customer_id": identity.customer_id}),
            ex=settings.tb_acl_cache_seconds,
        )
    except Exception:
        logger.warning("[TB-ACL] identity cache write failed", exc_info=True)
    return identity


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
    if is_expired(user_token):
        # Don't spend a ThingsBoard round-trip proving what the token already says.
        raise SessionExpired("token expired")

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
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            # Terminal, not transient — the session is dead or was revoked. Java's
            # retry logic already treated these as non-retryable; the difference is
            # that the user now gets told to sign in instead of to retry.
            logger.info("[TB-ACL] ThingsBoard rejected the caller's token (%s)", status)
            raise SessionExpired(f"thingsboard returned {status}") from exc
        logger.warning("[TB-ACL] could not resolve caller permissions", exc_info=True)
        raise PermissionCheckUnavailable(str(exc)) from exc
    except Exception as exc:
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
