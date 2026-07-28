"""Structural questions about the customer's organization tree.

"Which zones are under the EAST region", "how many branches under ZO Howrah",
"is there a NASIK branch" — answerable from hierarchy_node and the closure table,
with no ThingsBoard call at all.

SECURITY: the tree is assembled from the caller's AUTHORIZED LEAVES outward, never
from every node carrying the customer prefix. Loading by prefix would show a
region-scoped user the zones and branch counts of regions they cannot see — the
structure of a bank's network is itself information. Only the ancestors of branches
the caller may already read are included.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# hierarchy_node.node_type as written by the importer. Level names differ per bank
# ("NBG" for BOI, "RO" for Canara), so answers use the stored display name and these
# only decide which layer a question is asking about.
REGION_TYPES = ("FGMO", "NBG", "REGION", "RO")
ZONE_TYPES = ("ZO", "ZONE")
BRANCH_TYPES = ("BRANCH",)


@dataclass(frozen=True)
class Node:
    node_id: str
    parent_id: str | None
    node_type: str
    level: int
    display_name: str
    is_leaf: bool
    device_id: str | None


@dataclass
class ScopedTree:
    nodes: dict[str, Node] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    # leaf node_id -> every ancestor node_id, so an area can be resolved to devices.
    ancestors: dict[str, set[str]] = field(default_factory=dict)

    @property
    def leaves(self) -> list[Node]:
        return [node for node in self.nodes.values() if node.is_leaf]

    def of_type(self, types: Sequence[str]) -> list[Node]:
        wanted = {t.upper() for t in types}
        return sorted(
            (n for n in self.nodes.values() if n.node_type.upper() in wanted),
            key=lambda n: n.display_name,
        )

    def descendant_leaves(self, node_id: str) -> list[Node]:
        return [
            leaf
            for leaf in self.leaves
            if leaf.node_id == node_id or node_id in self.ancestors.get(leaf.node_id, ())
        ]

    def device_ids_under(self, node_id: str) -> list[str]:
        return [leaf.device_id for leaf in self.descendant_leaves(node_id) if leaf.device_id]


async def load_scoped_tree(
    db: "AsyncSession", prefix: str, branch_node_ids: Sequence[str]
) -> ScopedTree:
    """Build the tree upward from the branches the caller is authorized to see."""
    from sqlalchemy import select

    from app.db.models import BranchAncestorPath, HierarchyNode

    tree = ScopedTree()
    leaves = list(branch_node_ids)
    if not leaves:
        return tree

    rows = (
        await db.execute(
            select(BranchAncestorPath.node_id, BranchAncestorPath.ancestor_id).where(
                BranchAncestorPath.node_id.in_(leaves)
            )
        )
    ).all()
    ancestors: dict[str, set[str]] = {leaf: set() for leaf in leaves}
    for node_id, ancestor_id in rows:
        ancestors.setdefault(node_id, set()).add(ancestor_id)

    wanted = set(leaves) | {a for values in ancestors.values() for a in values}
    node_rows = (
        await db.execute(
            select(HierarchyNode).where(
                HierarchyNode.customer_id == prefix,
                HierarchyNode.node_id.in_(wanted),
            )
        )
    ).scalars().all()

    for row in node_rows:
        tree.nodes[row.node_id] = Node(
            node_id=row.node_id,
            parent_id=row.parent_id,
            node_type=str(row.node_type),
            level=int(row.node_level),
            display_name=str(row.display_name or row.node_id),
            is_leaf=bool(row.is_leaf),
            device_id=str(row.tb_device_id) if row.tb_device_id else None,
        )
    tree.ancestors = {k: v for k, v in ancestors.items() if k in tree.nodes}
    for node in tree.nodes.values():
        if node.parent_id and node.parent_id in tree.nodes:
            tree.children.setdefault(node.parent_id, []).append(node.node_id)
    for kids in tree.children.values():
        kids.sort(key=lambda nid: tree.nodes[nid].display_name)
    return tree


def _norm(value: str) -> str:
    """Strip the level word so "the EAST zone" matches a node named "NBG EAST"."""
    text = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    text = re.sub(
        r"\b(zo|zone|nbg|fgmo|region|ro|branch|bank of india|boi|the|of)\b", " ", text
    )
    return re.sub(r"\s+", " ", text).strip()


def find_area(tree: ScopedTree, question: str, types: Sequence[str] | None = None) -> Node | None:
    """The tree node a question names, matched on the significant words of its name.

    Longest match wins so "WEST II" is not shadowed by a node called "WEST".
    """
    asked = _norm(question)
    if not asked:
        return None
    pool = tree.of_type(types) if types else list(tree.nodes.values())
    best: Node | None = None
    for node in pool:
        name = _norm(node.display_name)
        if name and re.search(rf"\b{re.escape(name)}\b", asked):
            if best is None or len(name) > len(_norm(best.display_name)):
                best = node
    return best


def _names(nodes: Sequence[Node], limit: int = 20) -> str:
    shown = ", ".join(node.display_name for node in nodes[:limit])
    return f"{shown} (showing first {limit} of {len(nodes)})" if len(nodes) > limit else shown


def format_hierarchy_answer(tree: ScopedTree, question: str) -> tuple[str, dict[str, Any]]:
    text = question.lower()
    regions = tree.of_type(REGION_TYPES)
    zones = tree.of_type(ZONE_TYPES)
    branches = tree.leaves
    structured: dict[str, Any] = {
        "regions": [n.display_name for n in regions],
        "zones": [n.display_name for n in zones],
        "branch_count": len(branches),
    }

    if not tree.nodes:
        return (
            "No organization hierarchy is available for your authorized scope.",
            structured,
        )

    # "Is there a NASIK branch?" — an existence check, before any listing branch.
    if re.search(r"\bis there\b|\bdo (?:we|you) have\b|\bexists?\b", text):
        area = find_area(tree, question)
        if area is None:
            return (
                "No branch, zone or region matching that name is visible in your "
                "authorized scope.",
                structured,
            )
        leaves = tree.descendant_leaves(area.node_id)
        if area.is_leaf:
            return f"Yes — {area.display_name} is in your authorized scope.", structured
        return (
            f"Yes — {area.display_name} is in your authorized scope, with "
            f"{len(leaves)} branch(es): {_names(leaves)}.",
            structured,
        )

    area = find_area(tree, question)

    # Decide by what is being ASKED FOR, not merely by which words appear. Operators
    # call an NBG region "the EAST zone", so "which branches are under the EAST zone"
    # contains "zone" while asking for branches — keying off the word alone answered
    # with a zone count.
    wants_branches = "branch" in text
    wants_zones = not wants_branches and re.search(r"\bzones?\b", text) is not None

    if wants_zones and area is not None and area.node_type.upper() in {
        t.upper() for t in REGION_TYPES
    }:
        under = [tree.nodes[nid] for nid in tree.children.get(area.node_id, [])]
        if "how many" in text or "count" in text:
            return f"{len(under)} zone(s) under {area.display_name}.", structured
        return (
            f"Zones under {area.display_name}: {_names(under)}."
            if under
            else f"No zone is recorded under {area.display_name}.",
            structured,
        )

    if area is not None and (wants_branches or "under" in text or "list" in text):
        leaves = tree.descendant_leaves(area.node_id)
        structured["area"] = area.display_name
        structured["branches"] = [n.display_name for n in leaves]
        if "how many" in text or "count" in text:
            return f"{len(leaves)} branch(es) under {area.display_name}.", structured
        if not leaves:
            return f"No branch is recorded under {area.display_name}.", structured
        return f"Branches under {area.display_name}: {_names(leaves)}.", structured

    if "how many" in text and "zone" in text:
        return f"{len(zones)} zone(s) in your authorized scope.", structured
    if "how many" in text and ("region" in text or "fgmo" in text or "nbg" in text):
        return f"{len(regions)} region(s) in your authorized scope.", structured
    if "how many" in text and "branch" in text:
        return f"{len(branches)} branch(es) in your authorized scope.", structured

    if "most branches" in text or "most branch" in text:
        if not regions:
            return "No region level is recorded in your authorized scope.", structured
        ranked = sorted(
            regions, key=lambda n: len(tree.descendant_leaves(n.node_id)), reverse=True
        )
        top = ranked[0]
        return (
            f"{top.display_name} has the most branches under monitoring: "
            f"{len(tree.descendant_leaves(top.node_id))}.",
            structured,
        )

    # Default: the shape of the tree.
    lines = []
    for region in regions:
        child_zones = [tree.nodes[nid] for nid in tree.children.get(region.node_id, [])]
        lines.append(
            f"{region.display_name} ({len(child_zones)} zones, "
            f"{len(tree.descendant_leaves(region.node_id))} branches)"
        )
    if not lines:
        return (
            f"Your authorized scope covers {len(branches)} branch(es): {_names(branches)}.",
            structured,
        )
    return (
        f"Your authorized scope covers {len(regions)} region(s), {len(zones)} zone(s) "
        f"and {len(branches)} branch(es) — " + "; ".join(lines) + ".",
        structured,
    )
