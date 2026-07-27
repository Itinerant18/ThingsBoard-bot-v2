"""Field precedence resolution — direct port of Java FieldPrecedenceResolver.

Contract: docs/thingsboard-key-map.md §4 (gateway chain) and §5 (power). First
*parseable* hit wins: a present-but-garbage (UNKNOWN) value does NOT stop the
chain — the walk continues. No warnings are emitted here (the mapper owns those).
"""

import json
from collections.abc import Mapping
from typing import Any, NamedTuple

from app.normalization.values import NormalizedState, to_bool, to_double, to_state


class ResolvedField(NamedTuple):
    state: NormalizedState
    source_field: str | None
    raw_value: str | None


class ResolvedMetric(NamedTuple):
    value: float | None
    source_field: str | None


# §10 nested containers searched by the resolver for scalar values. NOTE: the
# mapper's findJsonString uses a *different* parent list — do not merge them.
_NESTED_PARENTS = ("rock", "gatewayStatus", "system_status", "ticketStatus", "rockAI")


def _find_in_nested_json(raw: Mapping[str, Any], target_key: str) -> str | None:
    for parent_key in _NESTED_PARENTS:
        parent = raw.get(parent_key)
        if parent is None:
            continue
        node: Any = parent
        if isinstance(parent, str):
            if not parent.strip().startswith("{"):
                continue
            try:
                node = json.loads(parent)
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(node, dict) and target_key in node:
            val = node[target_key]
            if isinstance(val, str | bool | int | float):
                return str(val)
    return None


def _resolve_first_state(raw: Mapping[str, Any], candidates: list[str]) -> ResolvedField:
    for key in candidates:
        value = raw.get(key)
        if value is None:
            value = _find_in_nested_json(raw, key)
        if value is None:
            continue
        text = str(value)
        state = to_state(text)
        if state != NormalizedState.UNKNOWN:
            return ResolvedField(state, key, text)
    return ResolvedField(NormalizedState.UNKNOWN, None, None)


def resolve_gateway_state(raw: Mapping[str, Any]) -> ResolvedField:
    # 1. Authoritative TB connectivity attribute.
    for key in ("active", "serverAttributes_active", "device_active", "gateway_active"):
        active = raw.get(key)
        if active is None:
            active = _find_in_nested_json(raw, key)
        if active is not None:
            text = str(active).strip()
            low = text.lower()
            if low in {"false", "0", "offline"}:
                return ResolvedField(NormalizedState.OFFLINE, key, text)
            if low in {"true", "1", "online"}:
                return ResolvedField(NormalizedState.ONLINE, key, text)
    # 2. Fallback chain (§4).
    return _resolve_first_state(
        raw,
        [
            "status_device_gateway_status",
            "statusbox_system_healthy",
            "system_status_statusbox_system_healthy",
            "rock_healthyStatus",
            "healthyStatus",
            "gwHealth",
            "gateway_sts",
            "gateway_status",
            "gatewayStatus_status",
            "rock_gateway_status",
            "status",
            "statusbox_system_on",
            "system_status_statusbox_system_on",
            "gatewayStatus_SYSTEM ON",
        ],
    )


def resolve_subsystem_state(
    raw: Mapping[str, Any], primary_field: str, *fallbacks: str
) -> ResolvedField:
    return _resolve_first_state(raw, [primary_field, *fallbacks])


def resolve_battery_voltage(raw: Mapping[str, Any]) -> ResolvedMetric:
    battery = to_double(raw.get("battery_status_battery_voltage"))
    if battery is not None:
        return ResolvedMetric(battery, "battery_status_battery_voltage")
    gateway = to_double(raw.get("gatewayStatus_battery_voltage"))
    if gateway is not None:
        return ResolvedMetric(gateway, "gatewayStatus_battery_voltage")
    return ResolvedMetric(None, None)


def resolve_ac_voltage(raw: Mapping[str, Any]) -> ResolvedMetric:
    value = to_double(raw.get("ac_status_ac_voltage"))
    return ResolvedMetric(value, "ac_status_ac_voltage" if value is not None else None)


def resolve_system_current(raw: Mapping[str, Any]) -> ResolvedMetric:
    value = to_double(raw.get("current_status_system_current"))
    return ResolvedMetric(value, "current_status_system_current" if value is not None else None)


def resolve_mains_on(raw: Mapping[str, Any]) -> bool | None:
    for key in (
        "statusbox_mains_on",
        "system_status_statusbox_mains_on",
        "MAINS ON",
        "gatewayStatus_MAINS ON",
    ):
        val = raw.get(key)
        if val is not None:
            resolved = to_bool(val)
            if resolved is not None:
                return resolved
    return None
