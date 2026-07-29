from app.normalization import build_snapshot
from app.query.fleet_health import aggregate_fleet_health, format_fleet_health


def _snap(branch: str, **states: str):
    raw = {"branchName": branch, **states}
    return build_snapshot(raw)


def test_aggregate_counts_deployed_modules_not_only_branches() -> None:
    snapshots = {
        "d1": _snap(
            "BOI-A",
            active="true",
            cctv_sts="ONLINE",
            ias_sts="ONLINE",
            bas_sts="N/A",
            fas_sts="FAULT",
            timeLock_sts="OFFLINE",
            accessControl_sts="N/A",
            alarmCount="1",
        ),
        "d2": _snap(
            "BOI-B",
            active="false",
            cctv_sts="ONLINE",
            ias_sts="N/A",
            bas_sts="ONLINE",
            fas_sts="N/A",
            timeLock_sts="N/A",
            accessControl_sts="N/A",
        ),
    }

    summary = aggregate_fleet_health(snapshots, ["d1", "d2"])

    assert summary.total == 8
    assert summary.healthy == 5
    assert summary.faulty == 1
    assert summary.offline == 2
    assert summary.categories["accessControl"].total == 0
    assert summary.open_alerts == 1


def test_missing_snapshot_is_unknown_gateway_only() -> None:
    summary = aggregate_fleet_health({}, ["d1"])
    assert summary.total == 1
    assert summary.unknown == 1
    assert summary.categories["cctv"].total == 0


def test_formats_category_and_overall_faq_answers() -> None:
    snapshots = {
        "d1": _snap("BOI-A", active="true", cctv_sts="ONLINE"),
        "d2": _snap("BOI-B", active="false", cctv_sts="FAULT"),
    }
    summary = aggregate_fleet_health(snapshots, ["d1", "d2"])

    cctv = format_fleet_health(summary, "Is the CCTV system healthy?", "cctv")
    assert "1 of 2" in cctv
    assert "1 faulty" in cctv

    overall = format_fleet_health(summary, "What is the real-time health status?")
    # "monitored devices" read as a branch count and contradicted the overview's
    # "N device(s) in your authorized scope"; the label now says what it counts.
    assert "2 branches" in overall and "4 monitored modules" in overall
    assert "2 are healthy (50.0%)" in overall

    acs = format_fleet_health(summary, "Are any ACS devices deployed?", "accessControl")
    assert acs == "No. The current scoped data shows 0 ACS devices deployed."


def test_formats_rankings_and_distribution() -> None:
    snapshots = {
        "d1": _snap("BOI-A", active="false", cctv_sts="FAULT", timeLock_sts="OFFLINE"),
        "d2": _snap("BOI-B", active="true", cctv_sts="ONLINE", timeLock_sts="ONLINE"),
    }
    summary = aggregate_fleet_health(snapshots, ["d1", "d2"])

    assert format_fleet_health(summary, "Which device category has the most faulty devices?").startswith("CCTV")
    assert "TLS 1" in format_fleet_health(
        summary, "Which device category has the most offline devices?"
    )
    assert "Gateway:" in format_fleet_health(
        summary, "What is the health distribution across all device categories?"
    )


def test_faulty_devices_are_named_not_merely_counted() -> None:
    """"Which specific device is currently faulty?" was answered with a demand for a
    device id. The per-branch states were already computed and never surfaced."""
    snapshots = {
        "d1": _snap("BOI-A", active="true", cctv_sts="FAULT"),
        "d2": _snap("BOI-B", active="true", cctv_sts="ONLINE"),
        "d3": _snap("BOI-C", active="false", cctv_sts="N/A"),
    }
    summary = aggregate_fleet_health(snapshots, ["d1", "d2", "d3"])

    faulty = format_fleet_health(summary, "Which specific device is currently faulty?")
    assert "BOI-A" in faulty and "CCTV" in faulty
    assert "BOI-B" not in faulty

    offline = format_fleet_health(summary, "List all devices that are currently offline")
    assert "BOI-C" in offline and "BOI-A" not in offline


def test_nothing_faulty_says_so_rather_than_listing_nothing() -> None:
    summary = aggregate_fleet_health({"d1": _snap("BOI-A", active="true")}, ["d1"])
    assert "No device is currently" in format_fleet_health(summary, "which devices are faulty?")
