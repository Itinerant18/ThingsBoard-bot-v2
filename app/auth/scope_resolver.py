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

    # Filter the branch NAMES by the same ACL, not just the device ids. The two lists
    # are positionally parallel (see branch_scope), so one pass keeps them aligned.
    if len(local.branch_node_ids) == len(local.tb_device_ids):
        pairs = [
            (node_id, device_id)
            for node_id, device_id in zip(local.branch_node_ids, local.tb_device_ids, strict=True)
            if str(device_id) in allowed
        ]
        permitted_nodes = [node_id for node_id, _ in pairs]
        permitted = [device_id for _, device_id in pairs]
    else:
        # branch_scope always emits parallel lists, so this is a corrupt cache entry
        # or a caller building ScopedBranches by hand. Positional pairing is then
        # meaningless: pairing anyway would attach the WRONG branch name to a device,
        # and zip(strict=True) would turn it into a 500 for every chat request.
        # Keep the device filter and surrender the names — a scope answer that lists
        # no branches is recoverable; one that lists the wrong branches is a leak.
        logger.error(
            "[TB-ACL] branch/device lists are not parallel (%d vs %d); dropping branch "
            "names for this request rather than risking a mismatched pairing",
            len(local.branch_node_ids),
            len(local.tb_device_ids),
        )
        permitted = [d for d in local.tb_device_ids if str(d) in allowed]
        permitted_nodes = []
    # The reverse direction of the ACL narrowing: devices ThingsBoard authorizes
    # that no local leaf claims. Those are invisible to every answer, and silence
    # about them reads as "you have 98" when the caller holds 100.
    unplaced = len(allowed - {str(d) for d in local.tb_device_ids})
    if unplaced:
        logger.warning(
            "[TB-ACL] %d device(s) authorized by ThingsBoard have no hierarchy leaf "
            "for %s; they cannot be named in any answer",
            unplaced,
            tenant.subject or tenant.customer_id,
        )

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

    # Branch nodes used to be returned unfiltered, on the reasoning that they are only
    # containers for name resolution and a branch is answerable solely through its
    # devices. That reasoning was wrong on both halves.
    #
    # These are LEAF nodes, not containers — leaf node_id IS the branch name — and
    # they were being answered directly: DeviceInventory replied "You have 104 branch
    # device(s) in scope: ..." straight from this list while ThingsBoard authorized
    # 100, naming BOI-BAS, BOI-BAHALDA, BOI-R-BAZAR and BOI-LOHARDAGA-CC to a Bank of
    # India head-office caller who holds none of them. Confirmed in the 2026-07-30
    # head-office audit. The unauthorized-branch gate in branch_names.py built its
    # "safe to mention" set from the same unfiltered list, so it would not have
    # refused those four either.
    #
    # hierarchy_answers.load_scoped_tree already re-applied the ACL for its own
    # answers, which is the tell: a boundary that each consumer has to remember to
    # re-apply is one that will be missed. Applying it here makes every consumer
    # correct by construction, and load_scoped_tree's second pass stays as defence in
    # depth — it is idempotent.
    #
    # Zone-level questions are unaffected: containers live in the hierarchy tree and
    # are rebuilt from whichever leaves survive, so a zone the caller still holds
    # devices under keeps its name.
    return ScopedBranches(
        branch_node_ids=permitted_nodes,
        tb_device_ids=permitted,
        unplaced_devices=unplaced,
    )


__all__ = ["PermissionCheckUnavailable", "resolved_scope"]
