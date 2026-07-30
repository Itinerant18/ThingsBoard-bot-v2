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
