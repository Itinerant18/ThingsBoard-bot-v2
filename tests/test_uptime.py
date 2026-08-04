"""Monthly uptime and fault history from the three mainDevices*/mainCCTVFault keys.

Fixtures are the real shapes read off production Redis on 2026-08-04, not invented
ones — including the fact that the three keys nest differently.
"""

from app.query.uptime import (
    build_report,
    format_fault_answer,
    format_uptime_answer,
    parse_cctv_faults,
    parse_device_faults,
    parse_uptime,
)

# Real payload from BOI-MALDATOWN.
ONTIME = (
    '{"IAS":{"integrated_alarm_system_on":{"lastTs":1785821495037,"monthly":'
    '{"2026-08":{"month_duration":2798.17,"downtime_minutes":41841.83,'
    '"uptime_score":0,"idle_score":0}}}},'
    '"CCTV":{"heartbeat_nvr_cctv_on":{"lastTs":1785756573217,"monthly":'
    '{"2026-08":{"month_duration":137.43,"downtime_minutes":44502.57,'
    '"uptime_score":0,"idle_score":0}}}}}'
)
# NOTE the different nesting: no "monthly" wrapper, no lastTs.
DEVICE_FAULT = '{"TLS":{"time_lock_tamper":{"2026-07":{"month_duration":1749.01,"fault_score":10}}}}'
CCTV_FAULT = (
    '{"disconnect":{"ch12":{"lastTs":0,"monthly":'
    '{"2026-08":{"month_duration":30,"fit_score":10}}},'
    '"ch9":{"lastTs":0,"monthly":{"2026-08":{"month_duration":90,"fit_score":10}}}}}'
)


def test_month_duration_and_downtime_sum_to_the_month() -> None:
    # This is the assumption the uptime percentage rests on. 2026-08 has 31 days =
    # 44640 minutes. If a future payload stops summing to the month, the percentage
    # means something else and this test says so before an operator is misled.
    rows = parse_uptime("BOI-MALDATOWN", {"mainDevicesOnTimeData": ONTIME})
    for row in rows:
        assert abs(row.total_minutes - 44640) < 1, (row.subsystem, row.total_minutes)


def test_uptime_percentage_is_computed_from_the_minutes() -> None:
    rows = {r.subsystem: r for r in parse_uptime("B", {"mainDevicesOnTimeData": ONTIME})}
    assert rows["IAS"].uptime_pct == 6.3   # 2798.17 / 44640
    assert rows["CCTV"].uptime_pct == 0.3  # 137.43 / 44640


def test_device_faults_parse_despite_having_no_monthly_wrapper() -> None:
    # The whole point of _months_of. A parser written against mainDevicesOnTimeData
    # alone returns nothing here, silently.
    rows = parse_device_faults("B", {"mainDevicesFaultData": DEVICE_FAULT})
    assert len(rows) == 1
    assert (rows[0].subsystem, rows[0].fault, rows[0].month) == ("TLS", "time_lock_tamper", "2026-07")
    assert rows[0].duration_minutes == 1749.01
    assert rows[0].score == 10


def test_cctv_faults_keep_the_channel() -> None:
    rows = {r.channel: r for r in parse_cctv_faults("B", {"mainCCTVFaultData": CCTV_FAULT})}
    assert set(rows) == {"ch12", "ch9"}
    assert rows["ch9"].duration_minutes == 90
    assert rows["ch9"].subsystem == "CCTV"


def test_report_counts_coverage_not_just_rows() -> None:
    snapshots = {
        "d1": {"branchName": "BOI-A", "mainDevicesOnTimeData": ONTIME},
        "d2": {"branchName": "BOI-B"},          # publishes nothing
        "d3": {"branchName": "BOI-C"},          # publishes nothing
    }
    report = build_report(snapshots)
    assert report.devices_seen == 3
    assert report.devices_with_uptime == 1


def test_uptime_answer_states_its_coverage() -> None:
    snapshots = {
        "d1": {"branchName": "BOI-A", "mainDevicesOnTimeData": ONTIME},
        "d2": {"branchName": "BOI-B"},
    }
    text, structured = format_uptime_answer(build_report(snapshots), "What is the uptime?")
    assert "2026-08" in text
    assert "1 of 2 branch(es)" in text, text
    assert structured["devices_with_data"] == 1


def test_uptime_answer_narrows_to_a_named_subsystem() -> None:
    snapshots = {"d1": {"branchName": "BOI-A", "mainDevicesOnTimeData": ONTIME}}
    _, structured = format_uptime_answer(
        build_report(snapshots), "What is the CCTV uptime this month?"
    )
    assert structured["subsystem"] == "CCTV"
    assert structured["uptime_pct"] == 0.3


def test_no_publisher_declines_honestly_rather_than_reporting_zero() -> None:
    report = build_report({"d1": {"branchName": "BOI-A"}})
    text, structured = format_uptime_answer(report, "What is the uptime?")
    assert "currently publishes monthly uptime data" in text
    assert structured["uptime_rows"] == 0


def test_a_non_uptime_question_is_left_alone() -> None:
    report = build_report({"d1": {"branchName": "BOI-A", "mainDevicesOnTimeData": ONTIME}})
    assert format_uptime_answer(report, "How many devices are offline?") is None
    assert format_fault_answer(report, "How many devices are offline?") is None


def test_fault_answer_separates_cctv_from_device() -> None:
    snapshots = {
        "d1": {
            "branchName": "BOI-A",
            "mainDevicesFaultData": DEVICE_FAULT,
            "mainCCTVFaultData": CCTV_FAULT,
        }
    }
    report = build_report(snapshots)
    cctv_text, cctv = format_fault_answer(report, "Show me the CCTV fault history")
    _, dev = format_fault_answer(report, "Show me the device fault history")
    assert cctv["kind"] == "CCTV" and cctv["total_fault_minutes"] == 120
    assert dev["kind"] == "device" and dev["total_fault_minutes"] == 1749.01
    assert "ch9" in cctv_text  # the worst channel is named
