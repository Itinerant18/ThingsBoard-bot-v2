"""Regional scope filtering for hierarchy nodes."""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.hierarchy.parser import normalize

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models import HierarchyNode


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegionalScope:
    name: str | None
    explicit: bool


# Region type prefixes for explicit detection
REGION_PREFIXES = {"FGMO", "LHO", "ZO", "CO", "RO", "RBO", "NBG"}


def should_fail_closed(scope: RegionalScope, matched_node_id: str | None) -> bool:
    return scope.explicit and scope.name is not None and matched_node_id is None


def names_match(left: str, right: str) -> bool:
    return normalize(left) == normalize(right)


def _matches_region_node(node: "HierarchyNode", scope_name: str) -> bool:
    """Check if a node matches the scope name (normalized equality or prefix+rest)."""
    normalized_scope = normalize(scope_name)
    normalized_display = normalize(node.display_name)

    # Exact match after normalization
    if normalized_scope == normalized_display:
        return True

    # Region-prefix + rest match: e.g., "ZO KOLKATA" matches "ZO Kolkata"
    # Split scope into prefix and rest
    scope_parts = normalized_scope.split(maxsplit=1)
    display_parts = normalized_display.split(maxsplit=1)

    return (
        scope_parts[0] in REGION_PREFIXES
        and len(scope_parts) > 1
        and len(display_parts) > 1
        and scope_parts[1] == display_parts[1]
    )


def filter_branches(
    scope: RegionalScope,
    leaf_nodes: list["HierarchyNode"],
    all_nodes: list["HierarchyNode"],
    ancestor_paths: dict[str, set[str]],
) -> list["HierarchyNode"]:
    """
    Filter leaf nodes by regional scope.

    - scope.name None -> leaf_nodes unchanged
    - Find first non-leaf node whose normalized display_name matches
    - Found -> leaves whose ancestor set contains that node_id
    - Not found: scope.explicit -> [] (LOG warning); else leaf_nodes
    """
    if scope.name is None:
        return leaf_nodes

    # Find matching non-leaf node
    matched_node_id: str | None = None
    for node in all_nodes:
        if not node.is_leaf and _matches_region_node(node, scope.name):
            matched_node_id = node.node_id
            break

    if matched_node_id is None:
        if scope.explicit:
            logger.warning("[SCOPE] Explicit region '%s' not found in hierarchy - fail closed", scope.name)
            return []
        return leaf_nodes

    # Filter leaves whose ancestor set contains the matched node
    result = []
    for leaf in leaf_nodes:
        ancestors = ancestor_paths.get(leaf.node_id, set())
        if matched_node_id in ancestors:
            result.append(leaf)

    return result


def extract_region(claims: dict[str, object]) -> RegionalScope:
    """
    Extract regional scope from JWT claims.

    EXPLICIT: firstName/lastName claims - if either, after normalize, starts with
    one of FGMO|LHO|ZO|CO|RO|RBO|NBG -> RegionalScope(name, explicit=True)
    GUESSED: else sub/email local part, split on ./_-, if any token in region prefix set,
    join tokens from it -> RegionalScope(name, explicit=False)
    Neither -> RegionalScope(None, False)
    """
    # EXPLICIT: firstName/lastName claims
    for claim_key in ("firstName", "lastName"):
        value = claims.get(claim_key)
        if isinstance(value, str):
            normalized = normalize(value)
            # Check if starts with region prefix
            for prefix in REGION_PREFIXES:
                if normalized.startswith(prefix + " ") or normalized == prefix:
                    return RegionalScope(name=value, explicit=True)

    # GUESSED: sub/email local part
    for claim_key in ("sub", "email", "preferred_username"):
        value = claims.get(claim_key)
        if isinstance(value, str):
            # Extract local part from email
            local_part = value.split("@")[0] if "@" in value else value
            # Split on . / _ -
            tokens = re.split(r"[./_-]+", local_part)
            # Find tokens that are region prefixes
            matched_prefixes = []
            for token in tokens:
                normalized_token = normalize(token)
                if normalized_token in REGION_PREFIXES:
                    matched_prefixes.append(normalized_token)

            if matched_prefixes:
                # Join tokens from the first matched prefix onwards
                # Find the first occurrence in tokens
                start_idx = -1
                for i, token in enumerate(tokens):
                    if normalize(token) in REGION_PREFIXES:
                        start_idx = i
                        break
                if start_idx >= 0:
                    region_name = " ".join(tokens[start_idx:])
                    return RegionalScope(name=region_name, explicit=False)

    return RegionalScope(name=None, explicit=False)


@dataclass(frozen=True)
class ScopedBranches:
    branch_node_ids: list[str]
    tb_device_ids: list[str]


async def branch_scope(
    session: "AsyncSession",
    prefix: str,
    scope: RegionalScope,
    redis: "Redis",
) -> ScopedBranches:
    """
    Load hierarchy nodes + closure rows for the customer (cache in Redis 60s),
    apply filter_branches, return {branch node_ids, tb_device_ids}.
    This is THE single scope gate - every data/query handler goes through it.
    """
    from sqlalchemy import select

    from app.db.models import BranchAncestorPath, HierarchyNode

    # SECURITY: the cached value is the FILTERED branch list, so the key must
    # include the scope. A prefix-only key would let a customer-wide user's
    # cached full inventory leak to a region-scoped user (and vice versa).
    scope_part = f"{'E' if scope.explicit else 'G'}:{normalize(scope.name) if scope.name else '*'}"
    cache_key = f"branch_scope:{prefix}:{scope_part}"
    cached = await redis.get(cache_key)
    if cached:
        import json
        data = json.loads(cached)
        return ScopedBranches(
            branch_node_ids=data["branch_node_ids"],
            tb_device_ids=data["tb_device_ids"],
        )

    # Load all nodes for this customer
    result = await session.execute(
        select(HierarchyNode).where(HierarchyNode.customer_id == prefix)
    )
    all_nodes = list(result.scalars().all())

    if not all_nodes:
        return ScopedBranches(branch_node_ids=[], tb_device_ids=[])

    # Build ancestor_paths from closure table
    ancestor_result = await session.execute(
        select(BranchAncestorPath).where(
            BranchAncestorPath.node_id.in_([n.node_id for n in all_nodes])
        )
    )
    ancestor_rows = ancestor_result.all()

    ancestor_paths: dict[str, set[str]] = {n.node_id: set() for n in all_nodes}
    for row in ancestor_rows:
        ancestor_paths[row.node_id].add(row.ancestor_id)

    # Filter leaves
    leaf_nodes = [n for n in all_nodes if n.is_leaf]
    filtered_leaves = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)

    branch_node_ids = [n.node_id for n in filtered_leaves]
    tb_device_ids = [str(n.tb_device_id) for n in filtered_leaves if n.tb_device_id]

    # Cache for 60 seconds
    import json
    await redis.setex(
        cache_key, 60, json.dumps({"branch_node_ids": branch_node_ids, "tb_device_ids": tb_device_ids})
    )

    return ScopedBranches(branch_node_ids=branch_node_ids, tb_device_ids=tb_device_ids)