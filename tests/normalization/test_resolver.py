from app.normalization.resolver import (
    resolve_battery_voltage,
    resolve_gateway_state,
    resolve_mains_on,
    resolve_subsystem_state,
)
from app.normalization.values import NormalizedState


def test_first_state_walks_past_unknown() -> None:
    # Present-but-garbage primary must NOT stop the chain; the parseable fallback wins.
    raw = {"cctv_sts": "garbage", "cctvStatus": "online"}
    resolved = resolve_subsystem_state(raw, "cctv_sts", "cctvStatus")
    assert resolved.state == NormalizedState.ONLINE
    assert resolved.source_field == "cctvStatus"


def test_battery_voltage_precedence() -> None:
    raw = {"battery_status_battery_voltage": "50.5", "gatewayStatus_battery_voltage": "45.0"}
    resolved = resolve_battery_voltage(raw)
    assert resolved.value == 50.5
    assert resolved.source_field == "battery_status_battery_voltage"


def test_battery_voltage_fallback() -> None:
    resolved = resolve_battery_voltage({"gatewayStatus_battery_voltage": "45.0"})
    assert resolved.value == 45.0
    assert resolved.source_field == "gatewayStatus_battery_voltage"


def test_battery_voltage_missing() -> None:
    resolved = resolve_battery_voltage({})
    assert resolved.value is None
    assert resolved.source_field is None


def test_gateway_authoritative_active_offline() -> None:
    resolved = resolve_gateway_state({"active": "false"})
    assert resolved.state == NormalizedState.OFFLINE
    assert resolved.source_field == "active"


def test_gateway_falls_through_to_chain() -> None:
    # active present but non-boolean -> not authoritative -> chain resolves status.
    resolved = resolve_gateway_state({"active": "maybe", "status": "online"})
    assert resolved.state == NormalizedState.ONLINE
    assert resolved.source_field == "status"


def test_gateway_nested_json_lookup() -> None:
    # status_device_gateway_status is missing top-level but present inside "rock" JSON string.
    raw = {"rock": '{"healthyStatus": "offline"}'}
    resolved = resolve_gateway_state(raw)
    assert resolved.state == NormalizedState.OFFLINE
    assert resolved.source_field == "healthyStatus"


def test_mains_on() -> None:
    assert resolve_mains_on({"MAINS ON": "true"}) is True
    assert resolve_mains_on({"statusbox_mains_on": "0"}) is False
    assert resolve_mains_on({}) is None
