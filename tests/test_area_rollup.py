"""Zone and region ranking, from the 2026-07-30 superlative misses.

    Q: Which zone currently has the worst overall performance?
    A: Your authorized scope covers 5 top-level area(s), 16 area(s) in total ...

The per-branch rows and the branch-to-area mapping both already existed. Nothing
joined them, so every zone-level metric question got the hierarchy summary.
"""

from app.query.area_rollup import area_of_branch, rank_areas, roll_up, unrecorded_metric
from app.query.hierarchy_answers import Node, ScopedTree


def _tree() -> ScopedTree:
    t = ScopedTree()
    t.nodes = {
        "HO": Node("HO", None, "HO", 0, "BANK OF INDIA", False, None),
        "EAST": Node("EAST", "HO", "NBG", 1, "NBG EAST", False, None),
        "WEST": Node("WEST", "HO", "NBG", 1, "NBG WEST", False, None),
        "ZOH": Node("ZOH", "EAST", "ZO", 2, "ZO HOWRAH", False, None),
        "B1": Node("B1", "ZOH", "BRANCH", 3, "BOI-LILUAH", True, "d1"),
        "B2": Node("B2", "ZOH", "BRANCH", 3, "BOI-DOBSON", True, "d2"),
        "B3": Node("B3", "WEST", "BRANCH", 3, "BOI-NASIK", True, "d3"),
    }
    t.ancestors = {
        "B1": {"ZOH", "EAST", "HO", "B1"},
        "B2": {"ZOH", "EAST", "HO", "B2"},
        "B3": {"WEST", "HO", "B3"},
    }
    return t


ROWS = [
    {"branch": "BOI-LILUAH", "total": 4, "healthy": 1, "offline": 3, "faulty": 0},
    {"branch": "BOI-DOBSON", "total": 4, "healthy": 1, "offline": 3, "faulty": 0},
    {"branch": "BOI-NASIK", "total": 4, "healthy": 4, "offline": 0, "faulty": 0},
]


def test_zone_question_groups_at_the_zone_level() -> None:
    mapping = area_of_branch(_tree(), "which zone has the worst health?")
    assert mapping["BOI-LILUAH"] == "ZO HOWRAH"
    assert mapping["BOI-NASIK"] == "NBG WEST"  # no zone above it; its area is the NBG


def test_region_question_groups_at_the_top_level() -> None:
    mapping = area_of_branch(_tree(), "which FGMO region has the worst health?")
    assert mapping["BOI-LILUAH"] == "NBG EAST"
    assert mapping["BOI-NASIK"] == "NBG WEST"


def test_rollup_sums_numeric_columns_per_area() -> None:
    rolled = {r["area"]: r for r in roll_up(ROWS, area_of_branch(_tree(), "zone"))}
    assert rolled["ZO HOWRAH"]["offline"] == 6
    assert rolled["ZO HOWRAH"]["branches"] == 2
    assert rolled["ZO HOWRAH"]["health_pct"] == 25.0
    assert rolled["NBG WEST"]["health_pct"] == 100.0


def test_worst_performance_names_the_zone() -> None:
    rolled = roll_up(ROWS, area_of_branch(_tree(), "zone"))
    sentence, ranked = rank_areas(rolled, "Which zone currently has the worst overall performance?")
    assert sentence.startswith("ZO HOWRAH has the worst overall health")
    assert ranked[0]["area"] == "ZO HOWRAH"


def test_most_offline_is_the_worst_zone_not_the_healthiest() -> None:
    # Polarity: "highest number of offline" asks for the WORST area.
    rolled = roll_up(ROWS, area_of_branch(_tree(), "zone"))
    sentence, _ = rank_areas(rolled, "Which zone has the highest number of offline devices?")
    assert sentence.startswith("ZO HOWRAH")


def test_branch_names_that_differ_between_telemetry_and_hierarchy_still_match() -> None:
    # Snapshots say "BRANCH LILUAH"; the hierarchy says "BOI-LILUAH".
    rows = [{"branch": "BRANCH LILUAH", "total": 4, "healthy": 0, "offline": 4}]
    rolled = roll_up(rows, area_of_branch(_tree(), "zone"))
    assert rolled and rolled[0]["area"] == "ZO HOWRAH"


def test_a_branch_that_cannot_be_placed_is_dropped_not_misfiled() -> None:
    rows = [{"branch": "SOMEWHERE ELSE", "total": 4, "healthy": 0, "offline": 4}]
    assert roll_up(rows, area_of_branch(_tree(), "zone")) == []


def test_unrecorded_metrics_decline_instead_of_substituting_a_number() -> None:
    assert "SLA" in (unrecorded_metric("Which zone has the best SLA compliance?") or "")
    assert "risk grade" in (
        unrecorded_metric("Which FGMO region has the most Critical risk cameras?") or ""
    )
    assert unrecorded_metric("Which zone has the worst health?") is None


def test_non_superlative_questions_are_left_alone() -> None:
    rolled = roll_up(ROWS, area_of_branch(_tree(), "zone"))
    assert rank_areas(rolled, "How many zones are there?") is None
    assert rank_areas(rolled, "Which zone is best and which is worst?") is None
