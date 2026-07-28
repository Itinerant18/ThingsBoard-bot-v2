"""Structural answers about the organization tree, and the scope they respect.

The tree mirrors the real BOI export: BANK -> NBG -> ZO -> BRANCH, with the level
names the fleet actually uses ("NBG EAST", "ZO HOWRAH", "BRANCH MALDA TOWN").

The scope test is the one that matters. The tree is assembled from the caller's
AUTHORIZED LEAVES outward, so a region-scoped user must not learn the zones or
branch counts of regions ThingsBoard does not authorize them for — the shape of a
bank's branch network is itself information.
"""

import pytest

from app.query.extract import KeywordIntentExtractor
from app.query.hierarchy_answers import (
    Node,
    ScopedTree,
    find_area,
    format_hierarchy_answer,
)

# (node_id, parent_id, node_type, level, display_name, is_leaf)
RAW = [
    ("BOI_HO", None, "HO", 0, "BANK OF INDIA", False),
    ("NBG_EAST", "BOI_HO", "NBG", 1, "NBG EAST", False),
    ("NBG_ODISHA", "BOI_HO", "NBG", 1, "NBG ODISHA", False),
    ("NBG_WEST2", "BOI_HO", "NBG", 1, "NBG West II", False),
    ("ZO_SILIGURI", "NBG_EAST", "ZO", 2, "ZO SILIGURI", False),
    ("ZO_HOWRAH", "NBG_EAST", "ZO", 2, "ZO HOWRAH", False),
    ("ZO_BARIPADA", "NBG_ODISHA", "ZO", 2, "ZO BARIPADA", False),
    ("ZO_NASIK", "NBG_WEST2", "ZO", 2, "ZO NASIK", False),
    ("BR_MALDA", "ZO_SILIGURI", "BRANCH", 3, "BRANCH MALDA TOWN", True),
    ("BR_RAIGANJ", "ZO_SILIGURI", "BRANCH", 3, "BRANCH RAIGANJ", True),
    ("BR_BALLY", "ZO_HOWRAH", "BRANCH", 3, "BRANCH BALLYBAZAR", True),
    ("BR_LILUAH", "ZO_HOWRAH", "BRANCH", 3, "BRANCH LILUAH", True),
    ("BR_BARIPADA", "ZO_BARIPADA", "BRANCH", 3, "BRANCH BARIPADA", True),
    ("BR_NASIK", "ZO_NASIK", "BRANCH", 3, "BRANCH NASIK", True),
]
PARENT = {node_id: parent for node_id, parent, *_ in RAW}


def _tree(leaf_ids: list[str]) -> ScopedTree:
    """Assemble exactly as load_scoped_tree does: outward from the authorized leaves."""
    keep: set[str] = set()
    ancestors: dict[str, set[str]] = {}
    for leaf in leaf_ids:
        chain, node = set(), PARENT.get(leaf)
        while node:
            chain.add(node)
            node = PARENT.get(node)
        ancestors[leaf] = chain
        keep |= chain | {leaf}

    tree = ScopedTree()
    for node_id, parent, node_type, level, name, is_leaf in RAW:
        if node_id in keep:
            tree.nodes[node_id] = Node(
                node_id=node_id,
                parent_id=parent,
                node_type=node_type,
                level=level,
                display_name=name,
                is_leaf=is_leaf,
                device_id=f"dev-{node_id}" if is_leaf else None,
            )
    tree.ancestors = ancestors
    for node in tree.nodes.values():
        if node.parent_id in tree.nodes:
            tree.children.setdefault(node.parent_id, []).append(node.node_id)
    for kids in tree.children.values():
        kids.sort(key=lambda nid: tree.nodes[nid].display_name)
    return tree


ALL_LEAVES = [node_id for node_id, _, _, _, _, is_leaf in RAW if is_leaf]
FULL = _tree(ALL_LEAVES)
HOWRAH_ONLY = _tree(["BR_BALLY", "BR_LILUAH"])


def answer(question: str, tree: ScopedTree = FULL) -> str:
    text, _ = format_hierarchy_answer(tree, question)
    return text


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def test_a_zone_scoped_caller_sees_only_their_own_ancestors() -> None:
    assert set(HOWRAH_ONLY.nodes) == {"BOI_HO", "NBG_EAST", "ZO_HOWRAH", "BR_BALLY", "BR_LILUAH"}


def test_a_zone_scoped_caller_cannot_count_another_regions_branches() -> None:
    reply = answer("How many branches are currently under the ODISHA zone?", HOWRAH_ONLY)
    assert "BARIPADA" not in reply
    assert "ODISHA" not in reply


def test_a_zone_scoped_caller_is_not_told_other_zones_exist() -> None:
    reply = answer("Which zones are currently under the EAST region?", HOWRAH_ONLY)
    assert "ZO HOWRAH" in reply
    assert "SILIGURI" not in reply


def test_an_empty_scope_answers_nothing() -> None:
    text, _ = format_hierarchy_answer(ScopedTree(), "how many branches are there?")
    assert "No organization hierarchy is available" in text


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


def test_zones_under_a_region() -> None:
    reply = answer("Which zones are currently under the EAST region?")
    assert "ZO HOWRAH" in reply and "ZO SILIGURI" in reply
    assert "BARIPADA" not in reply


def test_branches_under_a_zone() -> None:
    reply = answer("Which branches are currently under the ODISHA zone?")
    assert "BRANCH BARIPADA" in reply
    assert "MALDA" not in reply


def test_branch_count_under_a_zone() -> None:
    assert answer("How many branches are currently under the EAST zone?").startswith("4 branch(es)")


def test_a_two_word_area_name_is_not_shadowed_by_a_shorter_one() -> None:
    """"WEST II" must beat any node whose name is a prefix of it."""
    reply = answer("Which branches are currently under the WEST II zone?")
    assert "BRANCH NASIK" in reply


def test_existence_check_for_a_named_branch() -> None:
    assert answer("Is there a NASIK branch currently active in the system?").startswith("Yes")
    reply = answer("Is there a KOLKATA branch currently active in the system?")
    assert "No branch, zone or region matching that name" in reply


def test_region_with_the_most_branches() -> None:
    reply = answer("Which FGMO region currently has the most branches under monitoring?")
    assert reply.startswith("NBG EAST")


def test_tree_shape_is_the_default_answer() -> None:
    reply = answer("What is the current organization hierarchy under Bank of India?")
    assert "3 region(s), 4 zone(s) and 6 branch(es)" in reply
    assert "NBG EAST (2 zones, 4 branches)" in reply


def test_find_area_can_be_restricted_to_a_level() -> None:
    assert find_area(FULL, "under the EAST region", ("NBG",)).node_id == "NBG_EAST"
    assert find_area(FULL, "under the HOWRAH zone", ("ZO",)).node_id == "ZO_HOWRAH"


def test_device_ids_under_an_area_resolve_for_reuse_by_the_fleet_handlers() -> None:
    assert sorted(FULL.device_ids_under("ZO_HOWRAH")) == ["dev-BR_BALLY", "dev-BR_LILUAH"]


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "What is the current organization hierarchy under Bank of India?",
        "Which zones are currently under the EAST region?",
        "Which branches are currently under the ODISHA zone?",
        "How many branches are currently under the JH zone?",
        "Is there a NASIK branch currently active in the system?",
        "Which FGMO region currently has the most branches under monitoring?",
    ],
)
async def test_structure_questions_route_to_hierarchy_info(question: str) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == "hierarchy_info"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Show me all active alerts in the EAST zone right now", "alarm_detail"),
        ("Show me all users currently under the EAST zone", "user_directory"),
        ("Is the CCTV system healthy?", "fleet_health"),
    ],
)
async def test_state_questions_are_not_captured_by_the_structure_intent(
    question: str, expected: str
) -> None:
    """Naming an area but asking about STATE belongs to the handler that owns the data."""
    assert (await KeywordIntentExtractor().extract(question)).name == expected


@pytest.mark.asyncio
async def test_zone_scoped_health_is_not_answered_as_structure() -> None:
    """Zone-filtered HEALTH is a known gap — fleet_health has no area filter yet.

    What must hold today is only that it does not get answered as a structure
    question, which would state a branch count in reply to a health question.
    """
    got = await KeywordIntentExtractor().extract(
        "What is the current health status of all devices in the EAST zone?"
    )
    assert got.name != "hierarchy_info"
