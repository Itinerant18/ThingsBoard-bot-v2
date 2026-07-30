"""Superlative questions about cameras and about alarm age must name a winner.

From the 2026-07-30 head-office audit:

    Q: Which branch has the most cameras deployed?
    A: Cameras currently recording, by branch: BOI-MALDATOWN (32/32), ...

    Q: Which alarm type currently has the longest unresolved duration?
    A: Alarms by alarm type: BOI- BATTERY REVERSE 56, BOI- DVR/NVR OFF 9, ...

The first is real data in an order nobody asked for. The second groups correctly and
then ranks the groups by COUNT — answering "the most frequent type" to a question
about time.
"""

from datetime import UTC, datetime, timedelta

from app.query.alarm_answers import AlarmRecord, format_alarm_answer
from app.query.cctv_fleet import BranchRecording, FleetCctv, rank_cctv_branches

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _fleet() -> FleetCctv:
    return FleetCctv(
        branches=[
            BranchRecording(
                branch="BOI-BIG", device_id="d1", total_channels=32, recording=30,
                not_recording=2, compliant=30, cameras_configured=32, available=True,
            ),
            BranchRecording(
                branch="BOI-SMALL", device_id="d2", total_channels=8, recording=8,
                not_recording=0, compliant=8, cameras_configured=8, available=True,
            ),
        ],
        retention_days=30,
    )


def test_most_cameras_deployed_names_the_branch() -> None:
    sentence, rows = rank_cctv_branches(_fleet(), "Which branch has the most cameras deployed?")
    assert sentence.startswith("BOI-BIG has the most cameras: 32")
    assert rows[0]["branch"] == "BOI-BIG"


def test_fewest_cameras_names_the_other_branch() -> None:
    sentence, _ = rank_cctv_branches(_fleet(), "Which branch has the fewest cameras?")
    assert sentence.startswith("BOI-SMALL has the fewest cameras: 8")


def test_not_recording_wins_over_the_bare_camera_match() -> None:
    sentence, _ = rank_cctv_branches(
        _fleet(), "Which branch has the most cameras not recording?"
    )
    assert "BOI-BIG" in sentence and "channels not recording" in sentence


def test_a_non_superlative_cctv_question_is_left_alone() -> None:
    assert rank_cctv_branches(_fleet(), "How many cameras are recording?") is None
    assert rank_cctv_branches(_fleet(), "Which branch is best and which is worst?") is None


def _alarm(kind: str, branch: str, hours_open: float, active: bool = True) -> AlarmRecord:
    return AlarmRecord(
        alarm_id=f"{kind}-{branch}", alarm_type=kind, severity="MAJOR", branch=branch,
        zone="ZO X", region="NBG Y", device_id="dev",
        created_at=NOW - timedelta(hours=hours_open),
        ended_at=None if active else NOW, active=active, details={},
    )


def test_longest_unresolved_ranks_on_age_not_count() -> None:
    # BATTERY REVERSE is far more FREQUENT; DVR/NVR OFF has been open far LONGER.
    alarms = [_alarm("BATTERY REVERSE", f"B{i}", 1) for i in range(5)]
    alarms.append(_alarm("DVR/NVR OFF", "B9", 200))

    text, structured = format_alarm_answer(
        alarms, "Which alarm type currently has the longest unresolved duration?", NOW
    )
    assert text.startswith("DVR/NVR OFF has the longest"), text
    assert structured["ranked_by"] == "duration"


def test_longest_without_a_dimension_names_the_single_alarm() -> None:
    alarms = [_alarm("BATTERY REVERSE", "B1", 2), _alarm("DVR/NVR OFF", "B2", 50)]
    text, structured = format_alarm_answer(
        alarms, "Which alarm has been unresolved the longest?", NOW
    )
    assert text.startswith("DVR/NVR OFF at B2 has been unresolved the longest")
    assert structured["ranked_by"] == "duration"


def test_open_alarms_are_not_dropped_from_the_ranking() -> None:
    # AlarmRecord.duration is None while an alarm is open, so ranking on it directly
    # would have skipped every unresolved alarm — the exact population asked about.
    alarms = [_alarm("OLD OPEN", "B1", 300), _alarm("SHORT CLOSED", "B2", 1, active=False)]
    text, _ = format_alarm_answer(alarms, "Which alarm has been open the longest?", NOW)
    assert "OLD OPEN" in text


def test_count_ranking_still_works_for_a_most_question() -> None:
    alarms = [_alarm("BATTERY REVERSE", f"B{i}", 1) for i in range(5)]
    alarms.append(_alarm("DVR/NVR OFF", "B9", 200))
    text, _ = format_alarm_answer(alarms, "What is the most common alarm type?", NOW)
    assert text.startswith("BATTERY REVERSE has the most"), text
