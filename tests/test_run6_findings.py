"""Two defects the run-6 audit found by checking answers against ThingsBoard.

Both are the same class: a number stated as fact that the system could not actually
know. Neither was visible from the answer text alone — only from comparing it with
ThingsBoard.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.auth import scope_resolver
from app.auth.jwt import TenantContext
from app.auth.scope_resolver import resolved_scope
from app.config import Settings
from app.hierarchy.scope import ScopedBranches
from app.query.alarm_answers import AlarmRecord, format_alarm_answer

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
SETTINGS = Settings(database_url="postgresql+asyncpg://unused/unused")
TENANT = TenantContext(
    tenant_id="t",
    customer_id="c",
    subject="headoffice.security@bankofindia.bank.in",
    claims={},
    prefix="BOI",
    user_token="tok",
)


def _alarm(branch: str) -> AlarmRecord:
    return AlarmRecord(
        alarm_id=f"a-{branch}", alarm_type="X", severity="MAJOR", branch=branch,
        zone="ZO X", region="NBG Y", device_id="d",
        created_at=NOW - timedelta(hours=1), ended_at=None, active=True, details={},
    )


# --- A count off a capped read is a lower bound, not a count --------------------


def test_truncated_count_is_stated_as_a_lower_bound() -> None:
    # _MAX_ALARM_PAGES is 5 at page_size 100, so a fleet read stops at exactly 500.
    # Run 6 verified BALLYBAZAR reported as 500 where the truth is 9: the page cap
    # presented as a measurement.
    alarms = [_alarm(f"B{i}") for i in range(500)]
    text, structured = format_alarm_answer(
        alarms, "How many alarms are there?", NOW, truncated=True
    )
    assert text.startswith("at least 500"), text
    assert structured["count_is_lower_bound"] is True


def test_an_untruncated_count_is_still_stated_plainly() -> None:
    alarms = [_alarm("B1"), _alarm("B2")]
    text, structured = format_alarm_answer(alarms, "How many alarms are there?", NOW)
    assert text.startswith("2 matching alarm(s)")
    assert structured["count_is_lower_bound"] is False


def test_a_truncated_ranking_winner_is_also_a_lower_bound() -> None:
    alarms = [_alarm("BUSY") for _ in range(9)] + [_alarm("QUIET")]
    text, _ = format_alarm_answer(
        alarms, "Which branch has the most alarms?", NOW, truncated=True
    )
    assert "at least 9 alarm(s)" in text, text


# --- Devices ThingsBoard authorizes that the hierarchy cannot place -------------

LOCAL_IDS = [f"dev-{i:03d}" for i in range(104)]
NAMES = [f"BOI-BRANCH-{i:03d}" for i in range(104)]
# TB authorizes 100: 98 the hierarchy knows, plus 2 it has never heard of.
TB_AUTHORIZED = frozenset(LOCAL_IDS[:98]) | {"dev-lalpur", "dev-rasp5"}


@pytest.fixture
def local_and_tb(monkeypatch):
    async def fake_branch_scope(session, prefix, scope, redis):
        return ScopedBranches(branch_node_ids=list(NAMES), tb_device_ids=list(LOCAL_IDS))

    async def fake_acl(settings, token, redis):
        return TB_AUTHORIZED

    monkeypatch.setattr(scope_resolver, "branch_scope", fake_branch_scope)
    monkeypatch.setattr(scope_resolver, "authorized_device_ids", fake_acl)


@pytest.mark.asyncio
async def test_devices_with_no_hierarchy_leaf_are_counted(local_and_tb) -> None:
    # Run 6: ThingsBoard authorized 100, the answer said 98, and nothing indicated the
    # gap. Not naming an unplaceable device is right; implying it does not exist is not.
    scoped = await resolved_scope(None, None, TENANT, SETTINGS)  # type: ignore[arg-type]
    assert len(scoped.tb_device_ids) == 98
    assert scoped.unplaced_devices == 2


@pytest.mark.asyncio
async def test_a_complete_hierarchy_reports_none_unplaced(monkeypatch) -> None:
    async def fake_branch_scope(session, prefix, scope, redis):
        return ScopedBranches(branch_node_ids=NAMES[:98], tb_device_ids=LOCAL_IDS[:98])

    async def fake_acl(settings, token, redis):
        return frozenset(LOCAL_IDS[:98])

    monkeypatch.setattr(scope_resolver, "branch_scope", fake_branch_scope)
    monkeypatch.setattr(scope_resolver, "authorized_device_ids", fake_acl)
    scoped = await resolved_scope(None, None, TENANT, SETTINGS)  # type: ignore[arg-type]
    assert scoped.unplaced_devices == 0


def test_the_note_is_silent_when_nothing_is_unplaced() -> None:
    from app.query.handlers import _unplaced_note

    assert _unplaced_note(ScopedBranches([], [], unplaced_devices=0)) == ""
    note = _unplaced_note(ScopedBranches([], [], unplaced_devices=2))
    assert "2 device(s)" in note
    assert "hierarchy import" in note


# --- rank_branches must not answer a question about a metric it does not hold ---


def test_branch_ranking_stands_down_for_foreign_metrics() -> None:
    # Live, 2026-07-31: "Which branch has the most alarms?" answered "SEPL-DX2 has the
    # best overall health: 7 of 7 modules healthy". rank_branches fell through to
    # health_pct for any metric it did not recognise, so an alarm question got a
    # confidently named health winner — worse than the unranked list it replaced.
    from app.query.fleet_health import FleetHealthSummary, rank_branches

    summary = FleetHealthSummary(
        categories={},
        branches={
            "BOI-A": {"Gateway": "ONLINE", "CCTV": "ONLINE"},
            "BOI-B": {"Gateway": "OFFLINE", "CCTV": "OFFLINE"},
        },
        open_alerts=0,
    )
    for q in (
        "Which branch has the most alarms?",
        "Which branch has the highest alarm count in the report?",
        "Which branch has the most users?",
        "Which branch has the most cameras?",
    ):
        assert rank_branches(summary, q) is None, q

    # The metrics it DOES hold still rank.
    assert rank_branches(summary, "Which branch has the worst overall health?") is not None
    assert rank_branches(summary, "Which branch has the most offline devices?") is not None
