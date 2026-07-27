"""Intent key profiles, per docs/Telimetry-Attribute-key.md.

These previously asserted names like "battery_status_battery_voltage" and
"MAINS ON" — which appear on ZERO of the 128 production devices. The tests passed
while the bot could not answer battery voltage at all, because they pinned the
profile to itself rather than to anything real. They now assert the doc's keys.
"""

from app.normalization.flatten import request_keys
from app.query.key_profiles import INTENT_KEYS, keys_for


def test_power_metrics_use_the_flat_documented_keys() -> None:
    """The doc's canonical names, confirmed present on 92 of 128 devices."""
    assert keys_for("battery_voltage") == ["battery_voltage"]
    assert keys_for("ac_voltage") == ["ac_voltage"]
    assert keys_for("system_current") == ["system_current"]


def test_gateway_status_covers_flat_and_nested_forms() -> None:
    keys = keys_for("gateway_status")
    assert "gateway_sts" in keys  # flat heartbeat, present on 103 devices
    assert "gateway.systemStatus" in keys  # nested container field
    assert "gateway.powerStatus" in keys


def test_battery_health_is_voltage_plus_the_low_reverse_flags() -> None:
    """Doc: Low & Reverse / Reverse / Low / OK is a ladder over two flags."""
    keys = keys_for("battery_health")
    assert "battery_voltage" in keys
    assert "statusbox_battery_low" in keys
    assert "statusbox_battery_reverse" in keys


def test_recording_and_subsystem_aliases_share_profiles() -> None:
    assert keys_for("cctv_recording_info") == keys_for("cctv_recording_compliance")
    assert keys_for("subsystem_status") == keys_for("subsystem_fault_status")
    assert keys_for("subsystem_status") == keys_for("subsystem_alarm_status")


def test_no_profile_uses_the_superseded_underscore_spellings() -> None:
    """Guard against the old key-map creeping back in.

    Those names came from docs/thingsboard-key-map.md and matched nothing: 69 of the
    120 configured keys were absent from every device in the fleet.
    """
    dead = {
        "battery_status_battery_voltage",
        "ac_status_ac_voltage",
        "current_status_system_current",
        "gatewayStatus_battery_voltage",
        "gatewayStatus_MAINS ON",
        "cameraStatus_cctvStatus",
        "ticketStatus_BATTERY_LOW",
        "MAINS ON",
        "BATTERY LOW",
    }
    for intent, keys in INTENT_KEYS.items():
        assert not (dead & set(keys)), f"{intent} still references superseded keys"


def test_dotted_paths_are_reduced_before_hitting_thingsboard() -> None:
    """A dotted path is our addressing scheme; ThingsBoard has no such key, so the
    request must ask for the container instead."""
    wanted = request_keys(keys_for("gateway_status"))
    assert "gateway" in wanted
    assert not any("." in key for key in wanted)


def test_unknown_intent_returns_empty() -> None:
    assert keys_for("does_not_exist") == []
    assert keys_for("global_overview") == []  # handled intent, but no telemetry profile


def test_returned_list_is_a_copy() -> None:
    got = keys_for("gateway_status")
    got.append("mutated")
    assert "mutated" not in keys_for("gateway_status")
