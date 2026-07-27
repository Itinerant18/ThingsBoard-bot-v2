"""Nested ThingsBoard containers must become addressable dotted paths.

Shapes below are verbatim from device 01c61bb0-ab4c-11f0-91df-7ffa16af2ee9
(BOI-MALDATOWN) on 2026-07-27. Before this expansion, gateway.powerStatus,
battery_voltage and ac_voltage all resolved to None while ThingsBoard was holding
"Off", 14.0 and 220.0 — the values existed, nothing could reach them.
"""

import json

from app.normalization.flatten import expand_containers, request_keys

GATEWAY = {
    "logStatus": "Power Off",
    "powerStatus": "Off",
    "systemStatus": "Inactive",
    "healthStatus": "Inactive",
    "alarmFlag": False,
}


def test_live_fetch_shape_dicts() -> None:
    """The ThingsBoard client returns parsed dicts."""
    out = expand_containers({"gateway": GATEWAY})
    assert out["gateway.powerStatus"] == "Off"
    assert out["gateway.systemStatus"] == "Inactive"
    assert out["gateway"] == GATEWAY  # original preserved


def test_snapshot_shape_json_strings() -> None:
    """The Redis fleet snapshot stores every value as a string (live_sync._encode),
    so the same container arrives JSON-encoded. Both paths must resolve."""
    out = expand_containers({"gateway": json.dumps(GATEWAY)})
    assert out["gateway.powerStatus"] == "Off"


def test_nested_two_levels() -> None:
    """basSystemIntegration.basPowerStatus.batteryVoltage is the deepest path the
    key doc addresses."""
    raw = {
        "basSystemIntegration": {
            "basPowerStatus": {"batteryVoltage": 13.4, "mainStatus": "On"},
            "basAboutDevice": {"model": "NX-8"},
        }
    }
    out = expand_containers(raw)
    assert out["basSystemIntegration.basPowerStatus.batteryVoltage"] == 13.4
    assert out["basSystemIntegration.basAboutDevice.model"] == "NX-8"


def test_flat_key_wins_over_a_nested_alias() -> None:
    """A device sending BOTH keeps the flat one — the doc treats it as canonical."""
    out = expand_containers(
        {"battery_voltage": 14.0, "battery_status": {"battery_voltage": 99.0}}
    )
    assert out["battery_voltage"] == 14.0
    assert out["battery_status.battery_voltage"] == 99.0


def test_non_containers_are_left_alone() -> None:
    """Plain scalars and non-JSON strings must not be mangled — several devices send
    `gateway` as the bare string "Offline" rather than an object."""
    out = expand_containers({"gateway": "Offline", "count": 3, "missing": None})
    assert out == {"gateway": "Offline", "count": 3, "missing": None}


def test_malformed_json_is_ignored() -> None:
    out = expand_containers({"rock": "{not valid json"})
    assert out == {"rock": "{not valid json"}


def test_lists_are_kept_whole() -> None:
    """rock.HddINFO is a list; consumers iterate it, so it must survive intact and
    not be exploded into numeric indices."""
    hdd = [{"HDDSlot": 1, "HDDCapacity": "2000"}]
    out = expand_containers({"rock": {"HddINFO": hdd}})
    assert out["rock.HddINFO"] == hdd


def test_request_keys_reduces_paths_to_containers() -> None:
    got = request_keys(["gateway.powerStatus", "gateway.systemStatus", "battery_voltage"])
    assert got == ["gateway", "battery_voltage"]  # deduped, order preserved


def test_request_keys_passes_flat_keys_through() -> None:
    assert request_keys(["battery_voltage", "ac_voltage"]) == ["battery_voltage", "ac_voltage"]
