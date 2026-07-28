"""Acceptance test for the 40 answer-shapes the customer supplied.

These are the questions a BOI operator actually types, paired with the answer the
business expects back. They are asserted end-to-end in two layers:

  1. routing  — every question must reach the handler that owns its data
  2. content  — the formatter must state the right numbers, not merely respond

The alarm fixtures reproduce the REAL ThingsBoard payload shape, taken from live
BOI-MALDATOWN alarms. That matters more than it looks: ThingsBoard refreshes
`endTs` on every re-observation of an alarm that is still open, so an ACTIVE_UNACK
row carries a non-zero `endTs` far in the future of `startTs`. A fixture that omits
`endTs` on active alarms cannot fail when the resolution logic is inverted, which
is exactly how that bug survived a green suite.
"""

from datetime import UTC, datetime

import pytest

from app.normalization import build_snapshot
from app.query.alarm_answers import IST, format_alarm_answer, normalize_alarm
from app.query.extract import KeywordIntentExtractor
from app.query.fleet_health import aggregate_fleet_health, format_fleet_health

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _ms(*args: int) -> int:
    """Wall-clock IST -> epoch ms, because the supplied answers quote IST."""
    return int(datetime(*args, tzinfo=IST).timestamp() * 1000)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Routing: question -> handler that owns the data
# --------------------------------------------------------------------------- #

ROUTING_CASES = [
    ("What is the current status of our BOI system?", "fleet_health"),
    ("Which device category has the most faulty devices?", "fleet_health"),
    ("Which device category has the most offline devices?", "fleet_health"),
    ("Is the CCTV system healthy?", "fleet_health"),
    ("Is the Gateway system healthy?", "fleet_health"),
    ("How many TLS devices are offline?", "fleet_health"),
    ("Are any ACS devices deployed?", "fleet_health"),
    ("What needs attention now?", "fleet_health"),
    ("What is the current health status of all IAS devices?", "fleet_health"),
    ("What is the current health status of all BAS devices?", "fleet_health"),
    ("What is the current health status of all FAS devices?", "fleet_health"),
    ("What is the health distribution across all device categories?", "fleet_health"),
    ("What is the real-time health status of all monitored systems?", "fleet_health"),
    ("Which device category currently has the lowest health percentage?", "fleet_health"),
    ("Are there any active unresolved alarms?", "alarm_detail"),
    ("What is the current camera issue at BALLYBAZAR?", "alarm_detail"),
    ("Is there any camera disconnect alarm currently active?", "alarm_detail"),
    ("Is there any camera tamper alarm currently active?", "alarm_detail"),
    ("Is there any Integrated Alarm System activation currently active?", "alarm_detail"),
    ("What was the latest resolved alarm?", "alarm_detail"),
    ("What is the longest resolved alarm TAT shown?", "alarm_detail"),
    ("Are there any fire alarm activations right now?", "alarm_detail"),
    ("Are there any burglar alarm activations right now?", "alarm_detail"),
    ("Which branches have unresolved alarms in the displayed data?", "alarm_detail"),
    ("What alarm types are visible in the dashboard?", "alarm_detail"),
    ("What is the oldest currently active alarm?", "alarm_detail"),
    ("Which active alarm has been open the longest?", "alarm_detail"),
    ("What is the severity breakdown of the visible active alarms?", "alarm_detail"),
    ("What alarms were triggered in the last 24 hours?", "alarm_detail"),
    ("What alarms were triggered in the last hour?", "alarm_detail"),
    ("Are there alarms at BALLYBAZAR?", "alarm_detail"),
    ("Are there alarms at DOBSON?", "alarm_detail"),
    ("Are there alarms at LILUAH?", "alarm_detail"),
    ("Which branches currently need immediate attention?", "alarm_detail"),
    ("What is the current TAT for active alarms?", "alarm_detail"),
    ("What is the longest completed TAT in the displayed alarm history?", "alarm_detail"),
    ("Which zone has visible unresolved alarms?", "alarm_detail"),
    ("Are there any active critical alarms?", "alarm_detail"),
    ("Which region is currently active?", "device_inventory"),
    ("Which branch is visible on the map?", "device_inventory"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "expected"), ROUTING_CASES)
async def test_faq_question_reaches_the_right_handler(question: str, expected: str) -> None:
    got = await KeywordIntentExtractor().extract(question)
    assert got.name == expected


# --------------------------------------------------------------------------- #
# Alarm answers, against the real ThingsBoard payload shape
# --------------------------------------------------------------------------- #


def _raw_alarm(
    alarm_id: str,
    alarm_type: str,
    severity: str,
    branch: str,
    start_ms: int,
    *,
    clear_ms: int = 0,
    last_seen_ms: int | None = None,
) -> dict:
    cleared = clear_ms > 0
    return {
        "id": {"entityType": "ALARM", "id": alarm_id},
        "type": alarm_type,
        "name": alarm_type,
        "severity": severity,
        "originatorName": branch,
        "createdTime": start_ms,
        "startTs": start_ms,
        # An OPEN alarm still gets endTs refreshed by ThingsBoard; it is a last-seen
        # stamp, never a resolution. Defaulting it to "recently" is the whole point.
        "endTs": last_seen_ms if last_seen_ms is not None else (clear_ms or _ms(2026, 7, 28, 6, 0)),
        "clearTs": clear_ms,
        "cleared": cleared,
        "acknowledged": False,
        "status": "CLEARED_UNACK" if cleared else "ACTIVE_UNACK",
        "details": (
            {"prev": {"branchName": branch, "zoName": "ZO HOWRAH", "nbgName": "NBG EAST"}}
            if cleared
            else {"branchName": branch, "zoName": "ZO HOWRAH", "nbgName": "NBG EAST"}
        ),
    }


RAW_ALARMS = [
    _raw_alarm("a1", "BOI Camera Disconnect CH 15", "MAJOR", "BALLYBAZAR", _ms(2026, 7, 24, 15, 52)),
    _raw_alarm("a2", "BOI Camera Tamper", "WARNING", "DOBSON", _ms(2026, 7, 17, 15, 11)),
    _raw_alarm("a3", "BOI Camera Tamper", "WARNING", "BALLYBAZAR", _ms(2026, 7, 15, 18, 13)),
    _raw_alarm(
        "a4",
        "BOI Integrated Alarm System Activate",
        "MAJOR",
        "LILUAH",
        _ms(2026, 7, 26, 7, 24),
        clear_ms=_ms(2026, 7, 26, 7, 27),
    ),
    _raw_alarm(
        "a5",
        "BOI Fire Alarm System Activate",
        "MAJOR",
        "SEPL-DX2",
        _ms(2026, 7, 16, 8, 0),
        clear_ms=_ms(2026, 7, 16, 14, 0),
    ),
    _raw_alarm(
        "a6",
        "BOI Intrusion Alarm System Activate",
        "MAJOR",
        "SEPL-DX2",
        _ms(2026, 7, 23, 9, 0),
        clear_ms=_ms(2026, 7, 23, 9, 30),
    ),
    _raw_alarm(
        "a7",
        "BOI Camera Disconnect CH 3",
        "MAJOR",
        "BALLYBAZAR",
        _ms(2026, 7, 15, 10, 0),
        clear_ms=_ms(2026, 7, 15, 10, 20),
    ),
]

ALARMS = [normalize_alarm(raw, f"dev-{raw['id']['id']}") for raw in RAW_ALARMS]
assert all(alarm is not None for alarm in ALARMS)


def answer(question: str) -> str:
    text, _ = format_alarm_answer(list(ALARMS), question, now=NOW)  # type: ignore[arg-type]
    return text


def test_open_alarms_stay_open_despite_a_refreshed_end_timestamp() -> None:
    """The regression that made every ACTIVE_UNACK alarm read as resolved."""
    active = [alarm for alarm in ALARMS if alarm.active]  # type: ignore[union-attr]
    assert {alarm.alarm_type for alarm in active} == {
        "BOI Camera Disconnect CH 15",
        "BOI Camera Tamper",
    }
    assert all(alarm.ended_at is None and alarm.duration is None for alarm in active)


def test_resolved_tat_is_measured_to_the_clear_not_the_last_observation() -> None:
    ias = next(a for a in ALARMS if a.alarm_type.endswith("Integrated Alarm System Activate"))  # type: ignore[union-attr]
    assert int(ias.duration.total_seconds()) == 180
    fire = next(a for a in ALARMS if "Fire Alarm" in a.alarm_type)  # type: ignore[union-attr]
    assert int(fire.duration.total_seconds()) == 6 * 3600


def test_active_unresolved_alarms() -> None:
    reply = answer("Are there any active unresolved alarms?")
    assert "BALLYBAZAR" in reply and "Camera Disconnect CH 15" in reply
    assert "no end time" in reply


def test_camera_disconnect_currently_active() -> None:
    reply = answer("Is there any camera disconnect alarm currently active?")
    assert reply.startswith("Yes.")
    assert "Camera Disconnect CH 15" in reply and "BALLYBAZAR" in reply
    # The resolved CH 3 disconnect must not be reported as current.
    assert "CH 3" not in reply


def test_camera_tamper_currently_active_keeps_warning_severity() -> None:
    reply = answer("Is there any camera tamper alarm currently active?")
    assert reply.startswith("Yes.")
    assert "DOBSON" in reply and "BALLYBAZAR" in reply
    assert "WARNING" in reply
    assert "Disconnect" not in reply


def test_ias_activation_is_not_currently_active() -> None:
    reply = answer("Is there any Integrated Alarm System activation currently active?")
    assert "No matching active alarm" in reply
    assert "LILUAH" in reply  # the latest matching resolved one, as context


def test_latest_resolved_alarm() -> None:
    reply = answer("What was the latest resolved alarm?")
    assert "LILUAH" in reply and "Integrated Alarm System Activate" in reply
    assert "TAT 3 minutes" in reply


def test_longest_resolved_tat() -> None:
    reply = answer("What is the longest resolved alarm TAT shown?")
    assert "SEPL-DX2" in reply and "Fire Alarm" in reply
    assert "TAT 6 hours" in reply


def test_no_fire_or_burglar_activation_right_now() -> None:
    assert "No matching active alarm" in answer("Are there any fire alarm activations right now?")
    assert "No matching active alarm" in answer(
        "Are there any burglar alarm activations right now?"
    )


def test_branches_with_unresolved_alarms() -> None:
    reply = answer("Which branches have unresolved alarms in the displayed data?")
    assert "BALLYBAZAR" in reply and "DOBSON" in reply
    assert "LILUAH" not in reply and "SEPL-DX2" not in reply


def test_alarm_types_visible() -> None:
    reply = answer("What alarm types are visible in the dashboard?")
    for kind in ("Camera Disconnect", "Camera Tamper", "Integrated Alarm System Activate"):
        assert kind in reply


def test_oldest_and_longest_open_alarm_is_the_ballybazar_tamper() -> None:
    for question in (
        "What is the oldest currently active alarm?",
        "Which active alarm has been open the longest?",
    ):
        reply = answer(question)
        assert "BALLYBAZAR" in reply and "Camera Tamper" in reply
        # The supplied answer says "created on July 15, 2026 at 18:13" — IST, as the
        # operator's dashboard shows it, not the 12:43 UTC the same instant is stored as.
        assert "2026-07-15 18:13 IST" in reply


def test_severity_breakdown_of_active_alarms() -> None:
    reply = answer("What is the severity breakdown of the visible active alarms?")
    assert "Major 1" in reply and "Warning 2" in reply


def test_time_window_questions_report_nothing_recent() -> None:
    assert answer("What alarms were triggered in the last hour?").startswith(
        "No alarms were triggered in the last hour"
    )
    assert answer("What alarms were triggered in the last 24 hours?").startswith(
        "No alarms were triggered in the last 24 hours"
    )


def test_branches_needing_immediate_attention_rank_ballybazar_first() -> None:
    reply = answer("Which branches currently need immediate attention?")
    assert reply.index("BALLYBAZAR") < reply.index("DOBSON")


def test_current_tat_reports_live_duration_not_a_final_tat() -> None:
    reply = answer("What is the current TAT for active alarms?")
    assert "no final TAT until they end" in reply
    assert "12 days" in reply  # BALLYBAZAR tamper, open since 15 July


def test_longest_completed_tat() -> None:
    reply = answer("What is the longest completed TAT in the displayed alarm history?")
    assert "SEPL-DX2" in reply and "6 hours" in reply


def test_zone_with_unresolved_alarms() -> None:
    reply = answer("Which zone has visible unresolved alarms?")
    assert "ZO HOWRAH" in reply and "NBG EAST" in reply


def test_open_and_resolved_words_do_not_collide_as_substrings() -> None:
    """Both directions of the collision, since each answers with the opposite set.

    "unresolved" contains "resolved"; "currently resolved" contains "current".
    """
    unresolved = answer("Are there any active unresolved alarms?")
    assert "open for" in unresolved and "TAT" not in unresolved

    currently_resolved = answer("How many alarms are currently resolved?")
    assert "TAT" in currently_resolved and "no end time" not in currently_resolved

    # A time adverb alone still means open.
    assert "no end time" in answer("What alarms are open currently?")


def test_no_active_critical_alarms_names_the_highest_severity() -> None:
    reply = answer("Are there any active critical alarms?")
    assert reply.startswith("No active Critical alarms are visible.")
    assert "highest active severity is Major" in reply


# --------------------------------------------------------------------------- #
# Fleet health, against the customer's stated dashboard numbers
# --------------------------------------------------------------------------- #


def _branch(name: str, **states: str):
    return build_snapshot({"branchName": name, **states})


def _fleet():
    """52 modules across 21 branches: the distribution the answers quote.

    Gateway 19 healthy / 2 offline, CCTV 10 healthy / 1 faulty, IAS 4, BAS 7,
    FAS 6, TLS 2 healthy / 1 offline, ACS none deployed.

    The two offline branches must say cctv_sts="N/A" explicitly: snapshot.py infers
    an OFFLINE CCTV for any branch whose gateway is offline and whose CCTV state is
    unknown, which would deploy CCTV modules this fixture never intended.
    """
    snapshots, ids = {}, []
    for index in range(21):
        gateway_offline = index >= 19
        plan: dict[str, str] = {"active": "false" if gateway_offline else "true"}
        if index < 10:
            plan["cctv_sts"] = "ONLINE"
        elif index == 10:
            plan["cctv_sts"] = "FAULT"
        elif gateway_offline:
            plan["cctv_sts"] = "N/A"
        if index < 4:
            plan["ias_sts"] = "ONLINE"
        if index < 7:
            plan["bas_sts"] = "ONLINE"
        if index < 6:
            plan["fas_sts"] = "ONLINE"
        if index < 2:
            plan["timeLock_sts"] = "ONLINE"
        elif index == 2:
            plan["timeLock_sts"] = "OFFLINE"
        device_id = f"d{index}"
        ids.append(device_id)
        snapshots[device_id] = _branch(
            f"BOI-{index}", alarmCount="1" if index == 0 else "0", **plan
        )
    return aggregate_fleet_health(snapshots, ids)


FLEET = _fleet()


def test_fleet_matches_the_quoted_dashboard_totals() -> None:
    assert FLEET.total == 52
    assert FLEET.healthy == 48
    assert FLEET.faulty == 1
    assert FLEET.offline == 3
    assert FLEET.open_alerts == 1


def test_overall_system_status_answer() -> None:
    reply = format_fleet_health(FLEET, "What is the current status of our BOI system?")
    assert "48 are healthy (92.3%)" in reply
    assert "1 are faulty" in reply or "1 is faulty" in reply
    assert "3 are offline" in reply


def test_category_rankings_match_the_expected_answers() -> None:
    assert format_fleet_health(
        FLEET, "Which device category has the most faulty devices?"
    ).startswith("CCTV")
    offline = format_fleet_health(FLEET, "Which device category has the most offline devices?")
    assert "Gateway 2" in offline and "TLS 1" in offline
    lowest = format_fleet_health(
        FLEET, "Which device category currently has the lowest health percentage?"
    )
    assert lowest.startswith("TLS") and "66.7%" in lowest


def test_per_category_health_answers() -> None:
    cctv = format_fleet_health(FLEET, "Is the CCTV system healthy?", "cctv")
    assert "10 of 11" in cctv and "1 faulty" in cctv
    gateway = format_fleet_health(FLEET, "Is the Gateway system healthy?", "gateway")
    assert "19 of 21" in gateway and "2 offline" in gateway
    tls = format_fleet_health(FLEET, "How many TLS devices are offline?", "timeLock")
    assert "1 TLS device are offline" in tls or "1 TLS device is offline" in tls
    acs = format_fleet_health(FLEET, "Are any ACS devices deployed?", "accessControl")
    assert acs.startswith("No.") and "0 ACS devices deployed" in acs
    for label, key, count in (("IAS", "ias", 4), ("BAS", "bas", 7), ("FAS", "fas", 6)):
        reply = format_fleet_health(FLEET, f"health status of all {label} devices", key)
        assert f"{count} of {count} devices are healthy (100.0%)" in reply


def test_attention_answer_lists_every_priority_item() -> None:
    reply = format_fleet_health(FLEET, "What needs attention now?")
    assert "1 faulty CCTV" in reply
    assert "2 offline Gateway" in reply
    assert "1 offline TLS" in reply
    assert "1 reported open alert" in reply
