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

# NOTHING here classifies a level as "a region" or "a zone", on purpose. The bot
# serves every head office on the tenant, and the depth and naming of the tree differ
# not just between banks but WITHIN one — measured over the live export:
#
#   BOI     HO -> NBG -> ZO -> BRANCH   (65 devices, but also 3- and 5-level variants)
#   BOB     HO -> ZO  -> ZO -> BRANCH
#   SBI     HO -> ZO  -> ZO -> BRANCH
#   CANARA  HO -> HO  -> BRANCH
#   PNB     HO -> ZO  -> BRANCH
#
# So answers are expressed in the only two things that hold everywhere: a node's
# CHILDREN and its descendant LEAVES. The caller's word — zone, region, NBG, RO, LHO,
# circle — merely selects which of those two they meant.

# Level words to strip when matching a name, so "the EAST zone" finds "NBG EAST" and
# "RO Kolkata" is found by "Kolkata". Bank names are deliberately NOT in this list.
_LEVEL_WORDS = (
    "zo", "zone", "zonal", "nbg", "fgmo", "region", "regional", "ro", "rbo", "lho",
    "co", "circle", "branch", "ho", "head", "office", "the", "of", "under", "all",
)


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
        return sorted(
            (node for node in self.nodes.values() if node.is_leaf),
            key=lambda n: n.display_name,
        )

    @property
    def root(self) -> Node | None:
        """The shallowest node. Every tree here has exactly one — the importer always
        writes a head-office node, inserting one when the path lacks it."""
        candidates = [n for n in self.nodes.values() if n.parent_id not in self.nodes]
        return min(candidates, key=lambda n: n.level) if candidates else None

    @property
    def areas(self) -> list[Node]:
        """Every grouping level between the root and the branches, whatever it is
        called. This is what a caller means by "zones" or "regions"."""
        root = self.root
        return sorted(
            (
                node
                for node in self.nodes.values()
                if not node.is_leaf and (root is None or node.node_id != root.node_id)
            ),
            key=lambda n: (n.level, n.display_name),
        )

    @property
    def top_areas(self) -> list[Node]:
        """The root's direct children — the widest grouping this customer has."""
        root = self.root
        if root is None:
            return []
        return [self.nodes[nid] for nid in self.children.get(root.node_id, [])]

    def children_of(self, node_id: str) -> list[Node]:
        return [self.nodes[nid] for nid in self.children.get(node_id, [])]

    def sub_areas(self, node_id: str) -> list[Node]:
        """Child GROUPINGS, excluding branches.

        A customer whose grouping level holds branches directly (PNB, Canara) has
        none, and counting its branches as sub-areas reported one level of structure
        that does not exist.
        """
        return [node for node in self.children_of(node_id) if not node.is_leaf]

    def descendant_leaves(self, node_id: str) -> list[Node]:
        return [
            leaf
            for leaf in self.leaves
            if leaf.node_id == node_id or node_id in self.ancestors.get(leaf.node_id, ())
        ]

    def device_ids_under(self, node_id: str) -> list[str]:
        return [leaf.device_id for leaf in self.descendant_leaves(node_id) if leaf.device_id]


async def load_scoped_tree(
    db: "AsyncSession",
    prefix: str,
    branch_node_ids: Sequence[str],
    authorized_device_ids: Sequence[str] | None = None,
) -> ScopedTree:
    """Build the tree upward from the branches the caller is authorized to see.

    SECURITY: `branch_node_ids` is NOT the authorized set. resolved_scope() applies
    ThingsBoard's ACL to tb_device_ids only and deliberately leaves the branch
    containers untouched, on the reasoning that a branch is reachable only through
    its devices. That reasoning does not hold here — this module answers with branch
    NAMES and COUNTS and never reads a device — so the ACL has to be applied again.
    Measured against production: a Bank of India head-office token covers 104
    hierarchy branches while ThingsBoard authorizes 100, and without this filter the
    four unauthorized ones (BOI-BAS, BOI-BAHALDA, BOI-R-BAZAR, BOI-LOHARDAGA-CC)
    were named back to the caller.
    """
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
    if authorized_device_ids is not None:
        allowed = {str(device_id) for device_id in authorized_device_ids}
        unauthorized = {
            node_id
            for node_id, node in tree.nodes.items()
            if node.is_leaf and (node.device_id is None or node.device_id not in allowed)
        }
        for node_id in unauthorized:
            del tree.nodes[node_id]
        # An area whose every branch was dropped must disappear too, or its NAME still
        # discloses that the caller has something there. Repeat until stable, so a
        # region emptied only by losing its last zone also goes.
        while True:
            surviving = {
                ancestor
                for node in tree.nodes.values()
                if node.is_leaf
                for ancestor in ancestors.get(node.node_id, ())
            }
            orphans = {
                node_id
                for node_id, node in tree.nodes.items()
                if not node.is_leaf and node_id not in surviving
            }
            if not orphans:
                break
            for node_id in orphans:
                del tree.nodes[node_id]

    tree.ancestors = {k: v for k, v in ancestors.items() if k in tree.nodes}
    for node in tree.nodes.values():
        if node.parent_id and node.parent_id in tree.nodes:
            tree.children.setdefault(node.parent_id, []).append(node.node_id)
    for kids in tree.children.values():
        kids.sort(key=lambda nid: tree.nodes[nid].display_name)
    return tree


# An area filter is only in play when the question names a LEVEL — "the EAST zone",
# "NBG East", "RO Kolkata". Checking first keeps the tree query off the path of every
# fleet and alarm question, which is nearly all of them.
_NAMES_A_LEVEL = re.compile(
    r"\bzones?\b|\bzonal\b|\bregions?\b|\bregional\b|\bnbg\b|\bfgmo\b|\bro\b|\brbo\b"
    r"|\blho\b|\bcircles?\b|\bzo\b"
)


async def area_device_filter(
    db: "AsyncSession",
    prefix: str,
    branch_node_ids: Sequence[str],
    question: str,
    authorized_device_ids: Sequence[str] | None = None,
) -> tuple[list[str] | None, str | None]:
    """Devices under the area a question names, or (None, None) if it names none.

    Lets "health status in the EAST zone" reuse the fleet answers instead of growing
    a parallel set of area-shaped handlers. This only ever NARROWS: the tree is built
    from branches the caller is already authorized for, so an area named in the
    question can subtract devices from the scope but never add one.
    """
    if not branch_node_ids or not _NAMES_A_LEVEL.search(question.lower()):
        return None, None
    tree = await load_scoped_tree(db, prefix, branch_node_ids, authorized_device_ids)
    if not tree.nodes:
        return None, None
    area = find_area(tree, question, [n for n in tree.nodes.values() if not n.is_leaf])
    root = tree.root
    if area is None or (root is not None and area.node_id == root.node_id):
        # Naming the bank itself is not a filter — it IS the caller's whole scope.
        return None, None
    return tree.device_ids_under(area.node_id), area.display_name


def _norm(value: str) -> str:
    """Reduce a name or question to its distinguishing words.

    Level words go because the caller's vocabulary rarely matches the stored name —
    "the EAST zone" has to find "NBG EAST", "Kolkata" has to find "RO KOLKATA - I".
    Bank names deliberately stay: they are what distinguishes one head office's root
    node from another's, and stripping them would make every tenant's root identical.
    """
    text = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    text = re.sub(rf"\b(?:{'|'.join(_LEVEL_WORDS)})\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _shared_prefix_token(tree: ScopedTree) -> str:
    """The customer token every leaf name starts with, e.g. "boi" in "BOI-DOBSON".

    Nobody types it. "Which ZO does DOBSON branch belong to?" normalizes to
    "which does dobson belong to" and never matched "boi dobson", so every reverse
    lookup fell through to the whole-hierarchy dump. Derived from the names in hand
    rather than taken as a parameter, so it stays correct for a bank whose branches
    are not prefixed at all — then there is no shared token and nothing is stripped.
    """
    firsts = {
        _norm(node.display_name).split(" ", 1)[0]
        for node in tree.nodes.values()
        if node.is_leaf and _norm(node.display_name)
    }
    return firsts.pop() if len(firsts) == 1 else ""


def _match_names(node: Node, shared: str) -> list[str]:
    name = _norm(node.display_name)
    if shared and name.startswith(f"{shared} ") and name != shared:
        # Both forms: operators say "DOBSON", exports say "BOI-DOBSON".
        return [name, name[len(shared) + 1 :]]
    return [name]


def find_area(tree: ScopedTree, question: str, pool: Sequence[Node] | None = None) -> Node | None:
    """The tree node a question names, matched on the distinguishing words of its name.

    Longest match wins, so "WEST II" is not shadowed by a node called "WEST".
    """
    asked = _norm(question)
    if not asked:
        return None
    shared = _shared_prefix_token(tree)
    best: Node | None = None
    best_len = 0
    for node in pool if pool is not None else tree.nodes.values():
        for name in _match_names(node, shared):
            if name and re.search(rf"\b{re.escape(name)}\b", asked) and len(name) > best_len:
                best, best_len = node, len(name)
    return best


def _names(nodes: Sequence[Node], limit: int = 20) -> str:
    shown = ", ".join(node.display_name for node in nodes[:limit])
    return f"{shown} (showing first {limit} of {len(nodes)})" if len(nodes) > limit else shown


def format_hierarchy_answer(tree: ScopedTree, question: str) -> tuple[str, dict[str, Any]]:
    text = question.lower()
    areas = tree.areas
    top = tree.top_areas
    branches = tree.leaves
    structured: dict[str, Any] = {
        "top_areas": [n.display_name for n in top],
        "areas": [n.display_name for n in areas],
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
                (
                    "No branch, zone or region matching that name is visible in "
                    "your authorized scope."
                ),
                structured,
            )
        leaves = tree.descendant_leaves(area.node_id)
        if area.is_leaf:
            return f"Yes — {area.display_name} is in your authorized scope.", structured
        return (
            (
                f"Yes — {area.display_name} is in your authorized scope, with "
                f"{len(leaves)} branch(es): {_names(leaves)}."
            ),
            structured,
        )

    # "Which ZO does BALLYBAZAR belong to?" — the reverse lookup. Operators ask this
    # far more often than they ask for a listing, and the closure table already holds
    # the answer, so it only ever needed asking for.
    if re.search(r"\bbelongs? to\b|\bwhich .{0,24}\b(?:is|does) .+\b(?:in|under)\b", text):
        area = find_area(tree, question)
        if area is not None:
            chain = [
                tree.nodes[node_id]
                for node_id in sorted(
                    tree.ancestors.get(area.node_id, ()) if area.is_leaf else (),
                    key=lambda nid: tree.nodes[nid].level if nid in tree.nodes else 0,
                )
                if node_id in tree.nodes
            ]
            if not area.is_leaf and area.parent_id in tree.nodes:
                chain = [tree.nodes[area.parent_id]]
            if chain:
                structured["parents"] = [n.display_name for n in chain]
                return (
                    f"{area.display_name} sits under "
                    + " → ".join(n.display_name for n in chain)
                    + ".",
                    structured,
                )
            return f"{area.display_name} is the top of your authorized scope.", structured

    # "What are all the FGMO regions?" — a listing with no area named.
    if re.search(r"\b(?:all|list|what are|which are|name)\b", text) and re.search(
        r"\bzones?\b|\bregions?\b|\bnbg\b|\bfgmo\b|\bcircles?\b|\bbranch(?:es)?\b", text
    ) and find_area(tree, question) is None:
        if re.search(r"\bbranch(?:es)?\b", text):
            # "List all branches in the system" fell through to the whole-hierarchy
            # summary because this listing only recognised grouping levels.
            return f"{len(branches)} branch(es): {_names(branches)}.", structured
        wanted = top if re.search(r"\bregions?\b|\bnbg\b|\bfgmo\b|\bcircles?\b", text) else areas
        if not wanted:
            return "No grouping level is recorded in your authorized scope.", structured
        return (
            f"{len(wanted)} in your authorized scope: {_names(wanted)}.",
            structured,
        )

    area = find_area(tree, question)
    counting = "how many" in text or "count" in text

    # "What is the current device count per zone?" answered "3 area(s) in your
    # authorized scope" — the count of areas, not a count per area. Roll the leaves up
    # to each area instead. Pure traversal of the tree already loaded; no new data.
    if re.search(r"\b(?:per|each|by|breakdown by|across)\b", text) and re.search(
        r"\bzones?\b|\bregions?\b|\bareas?\b|\bnbg\b|\bfgmo\b|\bcircles?\b", text
    ):
        pool = top if re.search(r"\bregions?\b|\bnbg\b|\bfgmo\b", text) else areas
        if pool:
            counts = sorted(
                ((n.display_name, len(tree.descendant_leaves(n.node_id))) for n in pool),
                key=lambda pair: (-pair[1], pair[0]),
            )
            structured["per_area"] = dict(counts)
            return (
                "Branches per area: "
                + ", ".join(f"{name} {count}" for name, count in counts)
                + ".",
                structured,
            )

    # What is being ASKED FOR, not merely which words appear. Operators call an NBG
    # region "the EAST zone", so "which branches are under the EAST zone" says "zone"
    # while asking for branches. And "zone" vs "region" vs "NBG" is one bank's
    # vocabulary for a level another bank names differently, so neither word is taken
    # to mean a particular DEPTH — only "branch" is, because leaves are leaves
    # everywhere.
    wants_branches = "branch" in text
    wants_areas = not wants_branches and re.search(
        r"\bzones?\b|\bregions?\b|\bnbg\b|\bfgmo\b|\bcircles?\b|\bro\b|\blho\b", text
    )

    if area is not None:
        structured["area"] = area.display_name
        if wants_areas and not area.is_leaf:
            under = tree.sub_areas(area.node_id)
            structured["children"] = [n.display_name for n in under]
            if counting:
                return f"{len(under)} area(s) under {area.display_name}.", structured
            return (
                f"Under {area.display_name}: {_names(under)}."
                if under
                else f"{area.display_name} has no sub-areas; it holds branches directly.",
                structured,
            )
        if wants_branches or "under" in text or "list" in text:
            leaves = tree.descendant_leaves(area.node_id)
            structured["branches"] = [n.display_name for n in leaves]
            if counting:
                return f"{len(leaves)} branch(es) under {area.display_name}.", structured
            if not leaves:
                return f"No branch is recorded under {area.display_name}.", structured
            return f"Branches under {area.display_name}: {_names(leaves)}.", structured

    if counting and wants_branches:
        return f"{len(branches)} branch(es) in your authorized scope.", structured
    if counting and wants_areas:
        return f"{len(areas)} area(s) in your authorized scope.", structured

    if "most branches" in text or "most branch" in text:
        if not top:
            return (
                (
                    "Your authorized scope has no grouping level above its "
                    f"{len(branches)} branch(es)."
                ),
                structured,
            )
        ranked = sorted(top, key=lambda n: len(tree.descendant_leaves(n.node_id)), reverse=True)
        leader = ranked[0]
        return (
            (
                f"{leader.display_name} has the most branches under monitoring: "
                f"{len(tree.descendant_leaves(leader.node_id))}."
            ),
            structured,
        )

    # Default: the shape of the tree, described one level at a time so it reads the
    # same whether the customer has one grouping level or three.
    if not top:
        return (
            f"Your authorized scope covers {len(branches)} branch(es): {_names(branches)}.",
            structured,
        )
    lines = [
        f"{node.display_name} ({len(tree.sub_areas(node.node_id))} sub-areas, "
        f"{len(tree.descendant_leaves(node.node_id))} branches)"
        if tree.sub_areas(node.node_id)
        else f"{node.display_name} ({len(tree.descendant_leaves(node.node_id))} branches)"
        for node in top
    ]
    return (
        f"Your authorized scope covers {len(top)} top-level area(s), {len(areas)} area(s) "
        f"in total and {len(branches)} branch(es) — " + "; ".join(lines) + ".",
        structured,
    )
