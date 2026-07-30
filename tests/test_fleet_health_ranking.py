"""A superlative question must name the winner, not print the fleet total.

2026-07-30 head-office audit:

    Q: Which branch currently has the worst overall health?
    A: Across 98 branches, 153 monitored modules: 68 are healthy (44.4%)...

34 of 63 superlative questions came back as an aggregate or an unranked list. The
per-branch rows already existed inside FleetHealthSummary; nothing ranked them.
"""

from app.query.fleet_health import FleetHealthSummary, branch_rows, rank_branches

SUMMARY = FleetHealthSummary(
    categories={},
    branches={
        "BOI-GOOD": {"Gateway": "ONLINE", "CCTV": "ONLINE", "IAS": "ONLINE"},
        "BOI-MIXED": {"Gateway": "ONLINE", "CCTV": "OFFLINE", "IAS": "FAULT"},
        "BOI-BAD": {"Gateway": "OFFLINE", "CCTV": "OFFLINE", "IAS": "OFFLINE"},
    },
    open_alerts=0,
)


def test_rows_carry_the_detail_the_aggregate_discards() -> None:
    rows = {r["branch"]: r for r in branch_rows(SUMMARY)}
    assert rows["BOI-BAD"]["offline"] == 3
    assert rows["BOI-GOOD"]["health_pct"] == 100.0
    assert rows["BOI-MIXED"]["faulty"] == 1


def test_worst_health_names_the_branch() -> None:
    sentence, rows = rank_branches(SUMMARY, "Which branch currently has the worst overall health?")
    assert sentence.startswith("BOI-BAD has the worst overall health")
    assert rows[0]["branch"] == "BOI-BAD"


def test_best_health_names_the_other_branch() -> None:
    sentence, _ = rank_branches(SUMMARY, "Which branch currently has the best overall health?")
    assert sentence.startswith("BOI-GOOD has the best overall health")


def test_most_offline_ranks_on_the_offline_column() -> None:
    sentence, _ = rank_branches(SUMMARY, "Which branch has the highest number of offline devices?")
    assert "BOI-BAD" in sentence and "3 of 3" in sentence


def test_a_fleet_question_is_left_to_the_aggregate() -> None:
    assert rank_branches(SUMMARY, "What is the overall fleet health?") is None
    assert rank_branches(SUMMARY, "How many devices are offline?") is None


def test_ambiguous_question_does_not_guess() -> None:
    # "best" and "worst" both present — no defensible winner, fall through.
    assert rank_branches(SUMMARY, "Which branch is best and which is worst?") is None


def test_answer_only_uses_numbers_from_the_rows() -> None:
    import re

    sentence, rows = rank_branches(SUMMARY, "Which branch has the worst overall health?")
    top = rows[0]
    allowed = {str(top["healthy"]), str(top["total"]), str(top["offline"]), str(top["faulty"]),
               str(top["health_pct"]), str(int(top["health_pct"]))}
    for number in re.findall(r"\d+(?:\.\d+)?", sentence):
        assert number in allowed, f"{number} is not a value from the ranked row"


# --- Alarm type was a missing grouping dimension, not missing code ------------


def test_alarm_type_is_a_grouping_dimension() -> None:
    from app.query.alarm_answers import _group_dimension

    for question in (
        "what is the most common alarm type?",
        "what is the most frequent error type currently occurring?",
        "which device category has the highest alarm rate?",
    ):
        assert _group_dimension(question) == "alarm_type", question


def test_branch_and_zone_dimensions_still_win_where_they_should() -> None:
    from app.query.alarm_answers import _group_dimension

    assert _group_dimension("which branch has the most alarms?") == "branch"
    assert _group_dimension("which zone has the most alarms?") == "zone"


# --- "Show me all IAS devices" -----------------------------------------------

CATEGORY_SUMMARY = FleetHealthSummary(
    categories={},
    branches={
        "BOI-A": {"Gateway": "ONLINE", "IAS": "ONLINE", "CCTV": "OFFLINE"},
        "BOI-B": {"Gateway": "OFFLINE", "IAS": "FAULT"},
        "BOI-C": {"Gateway": "ONLINE"},  # no IAS deployed
    },
    open_alerts=0,
)


def test_lists_only_branches_where_the_subsystem_is_deployed() -> None:
    from app.query.fleet_health import category_listing

    text, rows = category_listing(CATEGORY_SUMMARY, "Show me all IAS devices")
    assert text.startswith("2 branch(es) with a IAS device:")
    assert [r["branch"] for r in rows] == ["BOI-A", "BOI-B"]
    assert "BOI-C" not in text


def test_gateway_listing_covers_every_branch() -> None:
    from app.query.fleet_health import category_listing

    text, rows = category_listing(CATEGORY_SUMMARY, "Show me all Gateway devices")
    assert len(rows) == 3


def test_a_category_with_nothing_deployed_says_so() -> None:
    from app.query.fleet_health import category_listing

    text, rows = category_listing(CATEGORY_SUMMARY, "Show me all FAS devices")
    assert text == "No FAS device is deployed in your authorized scope."
    assert rows == []


def test_non_listing_questions_are_left_to_the_health_answer() -> None:
    from app.query.fleet_health import category_listing

    # A health question about the same category must NOT become a listing.
    assert category_listing(CATEGORY_SUMMARY, "How many IAS devices are offline?") is None
    assert category_listing(CATEGORY_SUMMARY, "Is the IAS healthy?") is None
    # No category named at all.
    assert category_listing(CATEGORY_SUMMARY, "Show me all devices") is None
