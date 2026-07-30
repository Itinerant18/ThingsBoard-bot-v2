"""Zone/region questions, from the 2026-07-30 run-5 fell-through list.

format_hierarchy_answer already knew how to answer most of these; they never reached
it, and two of the ones that did were wrong.
"""

from app.query.hierarchy_answers import Node, ScopedTree, find_area, format_hierarchy_answer


def _tree() -> ScopedTree:
    t = ScopedTree()
    t.nodes = {
        "HO": Node("HO", None, "HO", 0, "BOI HO", False, None),
        "EAST": Node("EAST", "HO", "NBG", 1, "NBG EAST", False, None),
        "WEST": Node("WEST", "HO", "NBG", 1, "NBG WEST", False, None),
        "ZOH": Node("ZOH", "EAST", "ZO", 2, "ZO HOWRAH", False, None),
        "B1": Node("B1", "ZOH", "BRANCH", 3, "BOI-LILUAH", True, "d1"),
        "B2": Node("B2", "ZOH", "BRANCH", 3, "BOI-DOBSON", True, "d2"),
        "B3": Node("B3", "WEST", "BRANCH", 3, "BOI-NASIK", True, "d3"),
    }
    t.ancestors = {"B1": {"ZOH", "EAST", "HO"}, "B2": {"ZOH", "EAST", "HO"}, "B3": {"WEST", "HO"}}
    for nid, node in t.nodes.items():
        if node.parent_id:
            t.children.setdefault(node.parent_id, []).append(nid)
    return t


def test_branch_name_matches_without_the_customer_prefix() -> None:
    # "DOBSON" must find "BOI-DOBSON". Nobody types the prefix, so every reverse
    # lookup used to fall through to the whole-hierarchy dump.
    assert find_area(_tree(), "Which ZO does DOBSON branch belong to?").node_id == "B2"


def test_reverse_lookup_names_the_parent_chain() -> None:
    text, _ = format_hierarchy_answer(_tree(), "Which ZO does DOBSON branch belong to?")
    assert "BOI-DOBSON sits under" in text
    assert "ZO HOWRAH" in text


def test_count_under_a_named_area() -> None:
    text, _ = format_hierarchy_answer(_tree(), "How many branches are under the EAST zone?")
    assert text == "2 branch(es) under NBG EAST."


def test_per_zone_rollup_counts_each_area() -> None:
    # Was "3 area(s) in your authorized scope" — the count OF areas, not per area.
    text, structured = format_hierarchy_answer(_tree(), "What is the device count per zone?")
    assert text.startswith("Branches per area:")
    assert structured["per_area"]["NBG EAST"] == 2
    assert structured["per_area"]["NBG WEST"] == 1


def test_region_with_most_branches() -> None:
    text, _ = format_hierarchy_answer(
        _tree(), "Which FGMO region currently has the most branches under monitoring?"
    )
    assert text.startswith("NBG EAST has the most branches")


def test_list_all_branches() -> None:
    text, _ = format_hierarchy_answer(_tree(), "List all branches in the system")
    assert text.startswith("3 branch(es):")
    assert "BOI-DOBSON" in text


def test_no_shared_prefix_means_nothing_is_stripped() -> None:
    # A bank whose branches are not prefixed must not have a real first word removed.
    t = _tree()
    t.nodes["B1"] = Node("B1", "ZOH", "BRANCH", 3, "LILUAH MAIN", True, "d1")
    t.nodes["B2"] = Node("B2", "ZOH", "BRANCH", 3, "DOBSON ROAD", True, "d2")
    t.nodes["B3"] = Node("B3", "WEST", "BRANCH", 3, "NASIK CITY", True, "d3")
    assert find_area(t, "status of LILUAH MAIN").node_id == "B1"


# --- Routing: both fleet handlers must ask the hierarchy first ----------------


def test_hierarchy_trigger_fires_for_the_run5_fall_throughs() -> None:
    from app.query.handlers import _ASKS_HIERARCHY, _BRANCH_LISTING

    def fires(q: str) -> bool:
        q = q.lower()
        return bool(_ASKS_HIERARCHY.search(q) or _BRANCH_LISTING.search(q))

    for q in (
        "How many branches are currently under the WEST II zone?",
        "What is the current device count per zone?",
        "Which FGMO region currently has the most branches under monitoring?",
        "List all branches in the system",
        "How many branches are currently being monitored?",
        "How many devices are at each branch?",
        "How many total branches are there across all FGMO regions?",
    ):
        assert fires(q), q


def test_hierarchy_trigger_does_not_hijack_metric_or_fleet_questions() -> None:
    from app.query.handlers import _ASKS_HIERARCHY, _BRANCH_LISTING

    def fires(q: str) -> bool:
        q = q.lower()
        return bool(_ASKS_HIERARCHY.search(q) or _BRANCH_LISTING.search(q))

    for q in (
        "What is the battery voltage of Liluah branch?",
        "How many devices are offline right now?",
        "Is the CCTV at BALLYBAZAR recording?",
        "What is the overall fleet health?",
    ):
        assert not fires(q), q


def test_parent_chain_excludes_the_branch_itself() -> None:
    # The closure table stores each node as its own ancestor, so the chain read
    # "BOI-DOBSON sits under ... -> ZO HOWRAH -> BOI-DOBSON".
    t = _tree()
    t.ancestors["B2"] = {"ZOH", "EAST", "HO", "B2"}
    text, _ = format_hierarchy_answer(t, "Which ZO does DOBSON branch belong to?")
    assert text.count("BOI-DOBSON") == 1, text
    assert text.rstrip(".").endswith("ZO HOWRAH")


# --- Group C: geography and per-branch counts --------------------------------


def test_geo_and_per_branch_triggers() -> None:
    from app.query.handlers import _ASKS_COORDS, _ASKS_GEO, _ASKS_PER_BRANCH

    # Coordinates: reached, and recognised as wanting the numbers.
    for q in ("what is the latitude and longitude for each branch?",
              "where are the branches located geographically?",
              "show me the branch map"):
        assert _ASKS_GEO.search(q), q
    assert _ASKS_COORDS.search("what is the latitude and longitude for each branch?")
    assert not _ASKS_COORDS.search("show me the branch map")

    # A number PER branch, not a count OF branches.
    for q in ("how many devices are at each branch?", "show me the branch report"):
        assert _ASKS_PER_BRANCH.search(q), q

    # Must not hijack the neighbouring questions.
    for q in ("how many branches are there in total?",
              "what is the battery voltage of liluah?",
              "list all branches in the system"):
        assert not _ASKS_PER_BRANCH.search(q), q
        assert not _ASKS_GEO.search(q), q
