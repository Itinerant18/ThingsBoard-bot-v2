from app.query.key_profiles import keys_for


def test_gateway_status_keys() -> None:
    assert keys_for("gateway_status") == ["gateway", "status", "gatewayStatus"]


def test_battery_health_matches_battery_voltage() -> None:
    assert keys_for("battery_health") == keys_for("battery_voltage")
    assert "battery_status_battery_voltage" in keys_for("battery_voltage")


def test_recording_and_subsystem_aliases_share_profiles() -> None:
    assert keys_for("cctv_recording_info") == keys_for("cctv_recording_compliance")
    assert keys_for("subsystem_status") == keys_for("subsystem_fault_status")
    assert keys_for("subsystem_status") == keys_for("subsystem_alarm_status")


def test_keys_with_spaces_preserved() -> None:
    assert "BATTERY LOW" in keys_for("power_status")
    assert "MAINS ON" in keys_for("power_status")


def test_unknown_intent_returns_empty() -> None:
    assert keys_for("does_not_exist") == []
    assert keys_for("global_overview") == []  # handled intent, but no telemetry profile


def test_returned_list_is_a_copy() -> None:
    got = keys_for("gateway_status")
    got.append("mutated")
    assert "mutated" not in keys_for("gateway_status")
