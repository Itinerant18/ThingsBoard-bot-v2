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
    reply = answer("What is the current organization hierarchy?")
    assert "3 top-level area(s), 7 area(s) in total and 6 branch(es)" in reply
    assert "NBG EAST (2 sub-areas, 4 branches)" in reply


def test_find_area_can_be_restricted_to_a_candidate_pool() -> None:
    assert find_area(FULL, "under the EAST region", FULL.top_areas).node_id == "NBG_EAST"
    assert find_area(FULL, "under the HOWRAH zone").node_id == "ZO_HOWRAH"


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


# --------------------------------------------------------------------------- #
# Other head offices
#
# The bot serves every bank on the tenant, and their trees differ in depth and in
# what each level is called — measured over the live export. Nothing in the answers
# may assume BOI's four levels or its "NBG"/"ZO" vocabulary.
# --------------------------------------------------------------------------- #


def _custom_tree(rows: list[tuple[str, str | None, str, int, str, bool]]) -> ScopedTree:
    parents = {node_id: parent for node_id, parent, *_ in rows}
    tree = ScopedTree()
    for node_id, parent, node_type, level, name, is_leaf in rows:
        tree.nodes[node_id] = Node(node_id, parent, node_type, level, name, is_leaf, None)
    for node_id, _, _, _, _, is_leaf in rows:
        if not is_leaf:
            continue
        chain, cursor = set(), parents.get(node_id)
        while cursor:
            chain.add(cursor)
            cursor = parents.get(cursor)
        tree.ancestors[node_id] = chain
    for node in tree.nodes.values():
        if node.parent_id in tree.nodes:
            tree.children.setdefault(node.parent_id, []).append(node.node_id)
    for kids in tree.children.values():
        kids.sort(key=lambda nid: tree.nodes[nid].display_name)
    return tree


# CANARA: HO -> HO -> BRANCH. Two head-office levels and no zone at all.
CANARA = _custom_tree([
    ("C_HO", None, "HO", 0, "CANARA BANK", False),
    ("C_HO2", "C_HO", "HO", 1, "HO (Canara Bank)", False),
    ("C_RO1", "C_HO2", "RO", 2, "RO KOLKATA - I", False),
    ("C_BR1", "C_RO1", "BRANCH", 3, "CANARA-BEHALA", True),
])

# SBI: HO -> ZO -> ZO -> BRANCH, with level names carrying no level word at all.
SBI = _custom_tree([
    ("S_HO", None, "HO", 0, "STATE BANK OF INDIA", False),
    ("S_PATNA", "S_HO", "ZO", 1, "PATNA", False),
    ("S_PATNA2", "S_PATNA", "ZO", 2, "PATNA", False),
    ("S_BR", "S_PATNA2", "BRANCH", 3, "SBI LHO PATNA", True),
])

# PNB: HO -> ZO -> BRANCH. One grouping level.
PNB = _custom_tree([
    ("P_HO", None, "HO", 0, "PUNJAB NATIONAL BANK", False),
    ("P_ZO", "P_HO", "ZO", 1, "FAS", False),
    ("P_BR", "P_ZO", "BRANCH", 2, "PNB-FAS", True),
])


def test_a_bank_whose_grouping_level_is_called_ro_still_answers() -> None:
    text, _ = format_hierarchy_answer(CANARA, "Which branches are under RO KOLKATA - I?")
    assert "CANARA-BEHALA" in text


def test_a_bank_with_two_head_office_levels_reports_its_own_shape() -> None:
    text, _ = format_hierarchy_answer(CANARA, "What is the organization hierarchy?")
    assert "HO (Canara Bank)" in text
    assert "1 branch" in text


def test_a_level_name_carrying_no_level_word_is_still_matched() -> None:
    """SBI's levels are just "PATNA" — no "ZO", no "NBG" to key off."""
    text, _ = format_hierarchy_answer(SBI, "How many branches are under PATNA?")
    assert text.startswith("1 branch(es) under PATNA")


def test_a_single_grouping_level_does_not_read_as_a_missing_one() -> None:
    text, _ = format_hierarchy_answer(PNB, "What is the organization hierarchy?")
    assert "1 top-level area(s)" in text
    assert "FAS (1 branches)" in text


def test_bank_names_are_not_stripped_when_matching() -> None:
    """Stripping bank names would make every tenant's root node identical."""
    from app.query.hierarchy_answers import _norm

    assert _norm("BANK OF INDIA") == "bank india"
    assert _norm("STATE BANK OF INDIA") == "state bank india"
    assert _norm("NBG EAST") == "east"
    assert _norm("RO KOLKATA - I") == "kolkata i"


def test_asking_about_a_bank_by_name_does_not_match_another_banks_root() -> None:
    assert find_area(SBI, "under Bank of India") is None
    assert find_area(SBI, "under State Bank of India").node_id == "S_HO"


# --------------------------------------------------------------------------- #
# The area filter the fleet handlers reuse
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, rows, scalar_rows=None) -> None:
        self._rows = rows
        self._scalar_rows = scalar_rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def _asdict(self):
        return {}


class _FakeDb:
    """Replays the two queries load_scoped_tree issues, in order."""

    def __init__(self, tree: ScopedTree) -> None:
        self._tree = tree
        self.calls = 0

    async def execute(self, _stmt):
        self.calls += 1
        if self.calls == 1:
            rows = [
                (leaf, ancestor)
                for leaf, ancestors in self._tree.ancestors.items()
                for ancestor in ancestors
            ]
            return _FakeResult(rows)
        nodes = [
            type(
                "Row",
                (),
                {
                    "node_id": n.node_id,
                    "parent_id": n.parent_id,
                    "node_type": n.node_type,
                    "node_level": n.level,
                    "display_name": n.display_name,
                    "is_leaf": n.is_leaf,
                    "tb_device_id": n.device_id,
                },
            )()
            for n in self._tree.nodes.values()
        ]
        result = _FakeResult(nodes)
        result.scalars = lambda: type("S", (), {"all": lambda _self=None: nodes})()
        return result


@pytest.mark.asyncio
async def test_area_filter_narrows_to_the_named_area() -> None:
    from app.query.hierarchy_answers import area_device_filter

    db = _FakeDb(FULL)
    ids, name = await area_device_filter(
        db, "BOI", ALL_LEAVES, "health status of all devices in the HOWRAH zone"
    )
    assert name == "ZO HOWRAH"
    assert sorted(ids) == ["dev-BR_BALLY", "dev-BR_LILUAH"]


@pytest.mark.asyncio
async def test_area_filter_returns_nothing_when_no_level_is_named() -> None:
    """Most questions name no area, and must not pay for a tree query."""
    from app.query.hierarchy_answers import area_device_filter

    db = _FakeDb(FULL)
    ids, name = await area_device_filter(db, "BOI", ALL_LEAVES, "is the CCTV system healthy?")
    assert (ids, name) == (None, None)
    assert db.calls == 0


@pytest.mark.asyncio
async def test_naming_the_bank_itself_is_not_a_filter() -> None:
    from app.query.hierarchy_answers import area_device_filter

    db = _FakeDb(FULL)
    ids, name = await area_device_filter(
        db, "BOI", ALL_LEAVES, "device health across all zones of Bank of India"
    )
    assert (ids, name) == (None, None)


@pytest.mark.asyncio
async def test_area_filter_cannot_widen_beyond_the_callers_scope() -> None:
    """The tree is built from authorized leaves, so an area named by a zone-scoped
    caller resolves only within what they may already read."""
    from app.query.hierarchy_answers import area_device_filter

    db = _FakeDb(HOWRAH_ONLY)
    ids, name = await area_device_filter(
        db, "BOI", ["BR_BALLY", "BR_LILUAH"], "device health in the SILIGURI zone"
    )
    assert (ids, name) == (None, None)  # SILIGURI is not in their tree at all


# --------------------------------------------------------------------------- #
# The ThingsBoard ACL, applied a second time
#
# resolved_scope() filters tb_device_ids but deliberately leaves branch_node_ids
# whole, because a branch was only ever reachable through its devices. This module
# broke that assumption: it answers with branch NAMES and never reads a device. In
# production a BOI head-office token covers 104 hierarchy branches while
# ThingsBoard authorizes 100, and the four extras were being named back.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_branch_thingsboard_does_not_authorize_is_not_named() -> None:
    from app.query.hierarchy_answers import load_scoped_tree

    db = _FakeDb(FULL)
    authorized = ["dev-BR_BALLY", "dev-BR_LILUAH", "dev-BR_MALDA", "dev-BR_RAIGANJ"]
    tree = await load_scoped_tree(db, "BOI", ALL_LEAVES, authorized)

    names = {n.display_name for n in tree.leaves}
    assert names == {"BRANCH BALLYBAZAR", "BRANCH LILUAH", "BRANCH MALDA TOWN", "BRANCH RAIGANJ"}
    assert "BRANCH BARIPADA" not in names
    assert "BRANCH NASIK" not in names


@pytest.mark.asyncio
async def test_an_area_left_with_no_authorized_branch_disappears_entirely() -> None:
    """Reporting "NBG ODISHA (0 branches)" still discloses that it exists."""
    from app.query.hierarchy_answers import load_scoped_tree

    db = _FakeDb(FULL)
    tree = await load_scoped_tree(db, "BOI", ALL_LEAVES, ["dev-BR_BALLY", "dev-BR_LILUAH"])

    assert set(tree.nodes) == {"BOI_HO", "NBG_EAST", "ZO_HOWRAH", "BR_BALLY", "BR_LILUAH"}
    text, _ = format_hierarchy_answer(tree, "what is the organization hierarchy?")
    for hidden in ("ODISHA", "WEST II", "SILIGURI", "NASIK", "BARIPADA"):
        assert hidden not in text


@pytest.mark.asyncio
async def test_counts_reflect_the_authorized_set_not_the_hierarchy() -> None:
    from app.query.hierarchy_answers import load_scoped_tree

    db = _FakeDb(FULL)
    tree = await load_scoped_tree(db, "BOI", ALL_LEAVES, ["dev-BR_BALLY", "dev-BR_LILUAH"])
    text, structured = format_hierarchy_answer(tree, "how many branches are there?")
    assert text.startswith("2 branch(es)")
    assert structured["branch_count"] == 2


@pytest.mark.asyncio
async def test_an_area_filter_cannot_return_an_unauthorized_device() -> None:
    from app.query.hierarchy_answers import area_device_filter

    db = _FakeDb(FULL)
    ids, name = await area_device_filter(
        db,
        "BOI",
        ALL_LEAVES,
        "device health in the HOWRAH zone",
        ["dev-BR_BALLY"],  # LILUAH revoked by ThingsBoard
    )
    assert name == "ZO HOWRAH"
    assert ids == ["dev-BR_BALLY"]


@pytest.mark.asyncio
async def test_omitting_the_authorized_set_keeps_the_old_behaviour() -> None:
    """Callers that genuinely have no ACL to apply (tests, imports) still work."""
    from app.query.hierarchy_answers import load_scoped_tree

    tree = await load_scoped_tree(_FakeDb(FULL), "BOI", ALL_LEAVES)
    assert len(tree.leaves) == 6
