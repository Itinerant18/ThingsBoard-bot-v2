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


# Ladders for the three numeric power metrics.
#
# The flat name comes FIRST because docs/Telimetry-Attribute-key.md treats it as
# canonical and it is what devices actually carry (battery_voltage on 92 of 128).
# The dotted alias is the nested container field, materialised by
# normalization.flatten.expand_containers. The underscore spellings that used to be
# first here exist on NO device in the fleet — they are kept last only so a device
# that ever emits them still resolves.
_BATTERY_VOLTAGE_KEYS = (
    "battery_voltage",
    "battery_status.battery_voltage",
    "gatewayStatus.battery_voltage",
    "battery_status_battery_voltage",
    "gatewayStatus_battery_voltage",
)
_AC_VOLTAGE_KEYS = (
    "ac_voltage",
    "ac_status.ac_voltage",
    "gatewayStatus.ac_voltage",
    "ac_status_ac_voltage",
)
_SYSTEM_CURRENT_KEYS = (
    "system_current",
    "current_status.system_current",
    "gatewayStatus.system_current",
    "current_status_system_current",
)


def _first_number(raw: Mapping[str, Any], keys: tuple[str, ...]) -> ResolvedMetric:
    for key in keys:
        value = to_double(raw.get(key))
        if value is not None:
            return ResolvedMetric(value, key)
    return ResolvedMetric(None, None)


def resolve_battery_voltage(raw: Mapping[str, Any]) -> ResolvedMetric:
    return _first_number(raw, _BATTERY_VOLTAGE_KEYS)


def resolve_ac_voltage(raw: Mapping[str, Any]) -> ResolvedMetric:
    return _first_number(raw, _AC_VOLTAGE_KEYS)


def resolve_system_current(raw: Mapping[str, Any]) -> ResolvedMetric:
    return _first_number(raw, _SYSTEM_CURRENT_KEYS)


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
