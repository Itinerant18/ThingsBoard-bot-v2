import json

from app.normalization.snapshot import build_snapshot
from app.normalization.values import NormalizedState

HEALTHY = {
    "active": True,
    "cctv_sts": "ONLINE",
    "battery_status_battery_voltage": "87.5",
    "ac_status_ac_voltage": "230",
    "MAINS ON": True,
    "branchName": "DHANMONDI BRANCH 1",
    "device_id": "dev-001",
    "branch_id": "BR-DX1",
}

FAULTY = {
    "gatewayStatus_status": "offline",
    "cctv_sts": "FAULT",
    "BATTERY LOW": True,
    "HDD ERROR": True,
    "device_name": "MOTIJHEEL BRANCH",
}


def test_healthy_snapshot() -> None:
    snap = build_snapshot(HEALTHY)
    assert snap.gateway.state == NormalizedState.ONLINE
    assert snap.subsystems.cctv.state == NormalizedState.ONLINE
    assert snap.power.battery_voltage == 87.5
    assert snap.power.mains_on is True
    assert snap.identity.branch_name == "DHANMONDI BRANCH 1"


def test_faulty_snapshot() -> None:
    snap = build_snapshot(FAULTY)
    assert snap.gateway.state == NormalizedState.OFFLINE
    assert snap.subsystems.cctv.state == NormalizedState.FAULT
    assert snap.alerts.hdd_error is True
    assert snap.alerts.battery_low is True


def test_deterministic_and_json_serializable() -> None:
    a = build_snapshot(HEALTHY).to_dict()
    b = build_snapshot(HEALTHY).to_dict()
    assert a == b
    text = json.dumps(a)  # str-Enum must dump clean
    assert '"ONLINE"' in text


def test_na_subsystem_is_not_installed() -> None:
    snap = build_snapshot({"ias_sts": "N/A"})
    assert snap.subsystems.ias.state == NormalizedState.NOT_INSTALLED
    assert snap.subsystems.ias.installed is False


def test_two_battery_low_reads_have_different_types() -> None:
    # power.battery_low keeps bool|None; alerts.battery_low coerces None->False.
    snap = build_snapshot({})
    assert snap.power.battery_low is None
    assert snap.alerts.battery_low is False


def test_cctv_camera_with_no_status_key_counts_online() -> None:
    # §6 step 1: a camera object with no recognized status key is ONLINE.
    raw = {"CAMERAdETAILS": [{"channel": 1}, {"channel": 2, "status": "offline"}]}
    snap = build_snapshot(raw)
    assert snap.cctv.camera_count == 2
    assert snap.cctv.online_camera_count == 1


def test_cctv_details_accept_already_parsed_list() -> None:
    # v2 payloads arrive parsed from httpx .json(); must not require a JSON string.
    raw = {"rock_CAMERAdETAILS": [{"cameraStatus": "online"}, {"cameraStatus": "online"}]}
    snap = build_snapshot(raw)
    assert snap.cctv.online_camera_count == 2


def test_cctv_dynamic_fallback_online_when_cameras_present() -> None:
    # cctv_sts absent -> UNKNOWN; cameras online -> inferred ONLINE + installed True.
    raw = {"CAMERAdETAILS": [{"cameraStatus": "online"}]}
    snap = build_snapshot(raw)
    assert snap.subsystems.cctv.state == NormalizedState.ONLINE
    assert snap.subsystems.cctv.installed is True


def test_cctv_dynamic_fallback_offline_when_gateway_offline() -> None:
    raw = {"active": "false"}
    snap = build_snapshot(raw)
    assert snap.gateway.state == NormalizedState.OFFLINE
    assert snap.subsystems.cctv.state == NormalizedState.OFFLINE


def test_gateway_status_conflict_warning() -> None:
    snap = build_snapshot({"gateway": "online", "status": "offline"})
    assert any("conflict" in w for w in snap.warnings)


def test_cctv_channel_disconnect_ladder() -> None:
    raw = {
        "CAMERA DISCONNECT CH 1": "false",
        "CAMERA DISCONNECT CH 2": "true",
        "CAMERA DISCONNECT CH 3": "false",
    }
    snap = build_snapshot(raw)
    assert snap.cctv.camera_count == 3
    assert snap.cctv.online_camera_count == 2
