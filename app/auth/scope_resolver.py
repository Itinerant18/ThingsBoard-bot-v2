"""The one place a caller's authorized branch set is built.

There used to be two: app/deps.py (data endpoints, branch-name gate) and
app/query/handlers.py (chat). Two independent constructions of the same security
boundary is how a fix gets applied to one path and silently missed on the other —
the HTTP endpoints would look correct while every chat answer stayed over-scoped.
Both now call resolved_scope(); nothing else should call branch_scope() directly.
"""

import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TenantContext
from app.auth.tb_acl import PermissionCheckUnavailable, authorized_device_ids
from app.config import Settings
from app.hierarchy.scope import ScopedBranches, branch_scope, extract_region

logger = logging.getLogger(__name__)


async def resolved_scope(
    db: AsyncSession, redis: Redis, tenant: TenantContext, settings: Settings
) -> ScopedBranches:
    """Local hierarchy scope INTERSECTED with what ThingsBoard authorizes.

    ThingsBoard is the ceiling and the local hierarchy narrows it. The intersection
    direction matters: local scope alone over-grants (a prefix spans several TB
    customers), while TB's set alone would ignore regional scoping. Neither can
    widen the other.

    Raises PermissionCheckUnavailable when TB cannot confirm the caller's
    permissions — callers must fail closed rather than fall back to local scope,
    which is exactly the unchecked set this function exists to constrain.
    """
    if not tenant.prefix:
        return ScopedBranches(branch_node_ids=[], tb_device_ids=[])

    local = await branch_scope(db, tenant.prefix, extract_region(tenant.claims), redis)

    if not settings.enforce_tb_device_acl:
        # Deliberately loud: this leaves the app answering from prefix scope alone,
        # which is known to over-grant. Only for emergency rollback.
        logger.warning(
            "[TB-ACL] enforcement DISABLED — answering from local scope without "
            "ThingsBoard authorization (%d devices)",
            len(local.tb_device_ids),
        )
        return local

    allowed = await authorized_device_ids(settings, tenant.user_token or "", redis)

    permitted = [d for d in local.tb_device_ids if str(d) in allowed]
    dropped = len(local.tb_device_ids) - len(permitted)
    if dropped:
        # Expected whenever a prefix spans multiple TB customers; worth seeing,
        # because a sudden jump means the hierarchy and TB have diverged.
        logger.info(
            "[TB-ACL] narrowed scope for %s: %d -> %d devices (%d not authorized by TB)",
            tenant.subject or tenant.customer_id,
            len(local.tb_device_ids),
            len(permitted),
            dropped,
        )

    # Branch nodes are intentionally left as-is: they are containers used for name
    # resolution, and a branch is only ever answerable through its devices, which
    # are now filtered. Narrowing containers here would break zone-level questions
    # for users who legitimately hold only some devices under a zone.
    return ScopedBranches(branch_node_ids=local.branch_node_ids, tb_device_ids=permitted)


__all__ = ["PermissionCheckUnavailable", "resolved_scope"]
