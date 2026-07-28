from datetime import UTC, datetime

from app.query.alarm_answers import format_alarm_answer, normalize_alarm

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _alarm(**overrides):
    raw = {
        "id": {"id": "a1"},
        "type": "BOI Camera Disconnect CH 15",
        "severity": "MAJOR",
        "originatorName": "BOI-BALLYBAZAR",
        "createdTime": int(datetime(2026, 7, 24, 10, 22, tzinfo=UTC).timestamp() * 1000),
        "status": "ACTIVE_UNACK",
        **overrides,
    }
    result = normalize_alarm(raw, "d1")
    assert result is not None
    return result


def test_normalizes_active_and_cleared_thingsboard_alarms() -> None:
    active = _alarm()
    assert active.active is True
    assert active.ended_at is None

    cleared = _alarm(
        id={"id": "a2"},
        status="CLEARED_ACK",
        endTs=int(datetime(2026, 7, 24, 10, 25, tzinfo=UTC).timestamp() * 1000),
    )
    assert cleared.active is False
    assert int(cleared.duration.total_seconds()) == 180  # type: ignore[union-attr]


def test_formats_oldest_active_alarm_and_live_tat() -> None:
    old = _alarm()
    newer = _alarm(
        id={"id": "a2"},
        type="BOI Camera Tamper",
        severity="WARNING",
        originatorName="BOI-DOBSON",
        createdTime=int(datetime(2026, 7, 27, 12, 0, tzinfo=UTC).timestamp() * 1000),
    )

    answer, data = format_alarm_answer([newer, old], "What is the oldest active alarm?", NOW)
    assert "Camera Disconnect CH 15" in answer
    assert "BOI-BALLYBAZAR" in answer
    assert "no end time" in answer
    assert len(data["alarms"]) == 1

    tat, _ = format_alarm_answer([old], "What is the current TAT for active alarms?", NOW)
    assert "no final TAT" in tat
    assert "4 days" in tat


def test_formats_severity_type_filter_and_empty_window() -> None:
    major = _alarm()
    warning = _alarm(
        id={"id": "a2"},
        type="BOI Camera Tamper",
        severity="WARNING",
        originatorName="BOI-DOBSON",
    )

    breakdown, data = format_alarm_answer(
        [major, warning], "What is the severity breakdown of active alarms?", NOW
    )
    assert "Major 1" in breakdown and "Warning 1" in breakdown
    assert data["severity"] == {"MAJOR": 1, "WARNING": 1}

    tamper, _ = format_alarm_answer(
        [major, warning], "Is there any camera tamper alarm currently active?", NOW
    )
    assert "Camera Tamper" in tamper
    assert "Camera Disconnect" not in tamper

    empty, _ = format_alarm_answer([major], "What alarms were triggered in the last hour?", NOW)
    assert empty == "No alarms were triggered in the last hour."
