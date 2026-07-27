"""Branch-name resolution + unauthorized-branch question gate.

Port of the Java pair that turns "battery voltage of Liluah" into a device id safely:
- UserDataService.detectUnauthorizedBranchName — scan the question against ALL of the
  customer's branch names; if it names a branch outside the caller's scope, refuse
  BEFORE any data work.
- BranchAliasIndex + QueryIntentResolver.findBranchInQuestion — alias variants and
  longest-first explicit matching to resolve an in-scope branch name to its device.

Generic over customer prefix (no hardcoded bank names). The gate matches against every
leaf in the customer's hierarchy; resolution only ever returns devices from the CALLER'S
scoped set, so a resolver bug cannot bypass the scope gate in MetricHandler either.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HierarchyNode
from app.hierarchy.scope import ScopedBranches

# Aliases that are query vocabulary, not branch names (Java RESERVED_ALIAS_WORDS).
RESERVED_ALIAS_WORDS = frozenset(
    {
        "ACTIVE", "INACTIVE", "ONLINE", "OFFLINE", "STATUS", "WORKING", "DEVICE", "DEVICES",
        "BRANCH", "BRANCHES", "BRANCHS", "CAMERA", "CAMERAS", "BATTERY", "ALARM", "ALARMS",
        "SYSTEM", "HEALTH", "FAULT", "ERROR", "TOTAL", "COUNT", "GATEWAY", "TEST",
    }
)

# Zone containers may be referenced via these prefixes ("ZO HOWRAH", "NBG HOWRAH").
_ZONE_PHRASE_PREFIXES = ("ZO ", "NBG ", "RO ", "RBO ", "LHO ", "CO ", "FGMO ")


def normalize_key(value: str | None, prefix: str) -> str:
    """Uppercase, strip the customer prefix and BRANCH marker, unify separators."""
    if not value:
        return ""
    text = value.upper().replace(f"{prefix.upper()}-", "")
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.removeprefix("BRANCH ").strip()


def _compact(value: str) -> str:
    return value.replace(" ", "")


def alias_variants(alias: str, prefix: str) -> set[str]:
    """Spelling variants a user might type for one branch name (Java aliasVariants)."""
    variants: set[str] = set()
    normalized = normalize_key(alias, prefix)
    if not normalized:
        return variants
    variants.add(normalized)
    variants.add(_compact(normalized))
    for suffix in (" TESTING DEVICE", " DEVICE"):
        if normalized.endswith(suffix):
            simplified = normalized.removesuffix(suffix).strip()
            if simplified:
                variants.add(simplified)
                variants.add(_compact(simplified))
    return variants


@dataclass(frozen=True)
class BranchEntry:
    node_id: str
    display_name: str
    tb_device_id: str | None


@dataclass(frozen=True)
class BranchDirectory:
    """All of one customer's hierarchy names — the gate needs the FULL universe,
    not just the caller's slice, to recognize out-of-scope branch mentions."""

    prefix: str
    leaves: tuple[BranchEntry, ...]
    zones: tuple[BranchEntry, ...]  # non-leaf containers (ZO/RO/NBG...)


async def load_directory(db: AsyncSession, prefix: str) -> BranchDirectory:
    # ponytail: uncached DB read per question; hierarchy tables are small and indexed
    # by customer. Add a short Redis cache (like branch_scope's 60s) if chat QPS grows.
    result = await db.execute(select(HierarchyNode).where(HierarchyNode.customer_id == prefix))
    nodes = list(result.scalars().all())
    leaves = tuple(
        BranchEntry(n.node_id, n.display_name, str(n.tb_device_id) if n.tb_device_id else None)
        for n in nodes
        if n.is_leaf
    )
    zones = tuple(BranchEntry(n.node_id, n.display_name, None) for n in nodes if not n.is_leaf)
    return BranchDirectory(prefix=prefix, leaves=leaves, zones=zones)


@dataclass(frozen=True)
class BranchGateResult:
    unauthorized_branch: str | None = None  # display name of the out-of-scope branch named
    device_id: str | None = None  # resolved in-scope device
    branch_name: str | None = None  # display name of the resolved branch


def _is_part_of_zone_phrase(normalized_question: str, norm_name: str) -> bool:
    """'HOWRAH' matched but the user wrote 'ZO HOWRAH' — that names the zone container,
    not the branch (Java isPartOfZonePhrase, extended to all region prefixes)."""
    return any(prefix + norm_name in normalized_question for prefix in _ZONE_PHRASE_PREFIXES)


def _matches_explicit_alias(normalized_question: str, compact_question: str, alias: str) -> bool:
    """Java matchesExplicitAlias: word-boundary match for spaced/short aliases, compact
    containment (>=6 chars) so 'maldatown' still hits branch 'MALDA TOWN'."""
    if not alias:
        return False
    if " " in alias:
        return re.search(rf"(^|\s){re.escape(alias)}($|\s)", normalized_question) is not None
    if len(alias) >= 4 and re.search(rf"(^|\s){re.escape(alias)}($|\s)", normalized_question):
        return True
    compact_alias = _compact(alias)
    return len(compact_alias) >= 6 and compact_alias in compact_question


def _is_weak_alias(alias: str) -> bool:
    return len(alias) < 4 or alias in RESERVED_ALIAS_WORDS


def gate_and_resolve(
    question: str, directory: BranchDirectory, scoped: ScopedBranches
) -> BranchGateResult:
    """One pass over the question: refuse an out-of-scope branch mention, else resolve
    an in-scope branch name to its device id.

    SECURITY (Java parity): the gate matches against EVERY leaf in the customer's
    hierarchy, longest name first, and refuses before resolution. Zone/container names
    are always safe to *say* — a user who can see any branch under a zone may name the
    zone. Resolution draws candidates from the caller's scoped set only.
    """
    prefix = directory.prefix
    normalized_question = normalize_key(question, prefix)
    compact_question = _compact(normalized_question)
    scoped_node_ids = set(scoped.branch_node_ids)

    # Authorized-to-mention set: the caller's scoped leaves + every container name.
    auth_names: set[str] = set()
    for leaf in directory.leaves:
        if leaf.node_id in scoped_node_ids:
            auth_names.add(normalize_key(leaf.display_name, prefix))
            auth_names.add(normalize_key(leaf.node_id, prefix))
    for zone in directory.zones:
        auth_names.add(normalize_key(zone.display_name, prefix))
        auth_names.add(normalize_key(zone.node_id, prefix))

    # Gate: longest display name first so "MALDA TOWN MAIN" wins over "MALDA TOWN".
    for leaf in sorted(directory.leaves, key=lambda e: len(e.display_name), reverse=True):
        norm_name = normalize_key(leaf.display_name, prefix)
        norm_id = normalize_key(leaf.node_id, prefix)
        name_hit = len(norm_name) >= 4 and norm_name in normalized_question
        id_hit = len(norm_id) >= 5 and norm_id in normalized_question
        if not (name_hit or id_hit):
            continue
        # Java parity: the zone-phrase check is unconditional — node_id often normalizes
        # to the same text as the display name, so gating it on "name-only hit" would
        # wrongly flag "ZO HOWRAH" questions when a HOWRAH branch exists out of scope.
        if norm_name and _is_part_of_zone_phrase(normalized_question, norm_name):
            continue
        if norm_name not in auth_names and norm_id not in auth_names:
            return BranchGateResult(unauthorized_branch=leaf.display_name)

    # Resolution: alias index over SCOPED leaves only, longest alias first.
    alias_index: dict[str, BranchEntry] = {}
    for leaf in directory.leaves:
        if leaf.node_id not in scoped_node_ids or not leaf.tb_device_id:
            continue
        for source in (leaf.display_name, leaf.node_id):
            for variant in alias_variants(source, prefix):
                alias_index.setdefault(variant, leaf)
    for alias in sorted(alias_index, key=len, reverse=True):
        if _is_weak_alias(alias):
            continue
        if _matches_explicit_alias(normalized_question, compact_question, alias):
            hit = alias_index[alias]
            return BranchGateResult(device_id=hit.tb_device_id, branch_name=hit.display_name)

    return BranchGateResult()
