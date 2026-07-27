"""Real-key resolution ladders — port of Java AnswerSupport.

The snapshot models a narrow key set; the production fleet's fault/alarm/battery
detail lives across many alternate keys (ticketStatus_*, iasBasFasStatus_*, *_count,
statusbox_battery_low, ...). These read the FULL raw device dict (BranchSnapshot.raw_data,
which _load_raw populates from all server+client attributes), so answers resolve the
real keys instead of under-reporting. See docs/real-device-keys.md.

Note: resolve_boolean here has its own true/false keyword sets (Java AnswerSupport),
narrower than values.to_bool — ported verbatim so results match the Java bot.
"""

from collections.abc import Mapping
from typing import Any

_TRUE = {"true", "1", "on", "healthy", "online"}
_FALSE = {"false", "0", "off", "offline", "inactive"}
_SKIP = {"", "n/a", "null"}

# Every key the ladders below read. _load_raw requests these (union with the intent's
# key_profile) as an EXPLICIT timeseries keys list, so a device whose fault/count keys
# are telemetry (not attributes) still gets imported — and the request always carries a
# `keys` param, which some ThingsBoard versions require. Keep in sync with the ladders.
LADDER_KEYS: frozenset[str] = frozenset(
    {
        "statusbox_battery_low",
        "system_status_statusbox_battery_low",
        "BATTERY LOW",
        "gatewayStatus_BATTERY LOW",
        "statusbox_battery_reverse",
        "system_status_statusbox_battery_reverse",
        "BATTERY REVERSE",
        "gatewayStatus_BATTERY REVERSE",
        "ticketStatus_IAS_FAULT",
        "intrusion_alarm_system_fault",
        "INTRUSION ALARM SYSTEM FAULT",
        "iasBasFasStatus_INTRUSION ALARM SYSTEM FAULT",
        "BASfaultCOUNT",
        "ticketStatus_FAS_FAULT",
        "fireAlarmSystem_fault",
        "FIRE ALARM SYSTEM FAULT",
        "fire_alarm_system_fault",
        "iasBasFasStatus_FIRE ALARM SYSTEM FAULT",
        "ticketStatus_TLS_TAMPER",
        "ticketStatus_TLS_OFF",
        "ticketStatus_ACS_TAMPER",
        "ticketStatus_ACS_OFF",
        "HDD ERROR",
        "ticketStatus_HDD_ERROR",
        "ticketStatus_NVR_OFF",
        "cameraStatus_HDD ERROR",
        "ticketStatus_IAS_ACTIVATE",
        "ticketStatus_IAS_INT_ACTIVATE",
        "INTRUSION ALARM SYSTEM ACTIVATE",
        "iasBasFasStatus_INTRUSION ALARM SYSTEM ACTIVATE",
        "ticketStatus_FAS_ACTIVATE",
        "FIRE ALARM SYSTEM ACTIVATE",
        "iasBasFasStatus_FIRE ALARM SYSTEM ACTIVATE",
        "ticketStatus_TLS_DOOR_OPEN",
        "time_lock_door_open_count",
        "ticketStatus_ACS_DOOR_OPEN",
        "access_control_door_open_count",
        "CAMERA DISCONNECT",
        "ticketStatus_CAM_DIS",
        "ticketStatus_CAM_TAMPER",
    }
)


def resolve_boolean(raw: Mapping[str, Any], *keys: str) -> bool | None:
    """First key with a parseable boolean wins; blank/N/A/null are skipped."""
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text.lower() in _SKIP:
            continue
        low = text.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())  # Java parseInt: rejects "3.0"
    except ValueError:
        return None


def resolve_from_count(raw: Mapping[str, Any], key: str) -> bool | None:
    """A count key resolves to a boolean flag: present and > 0 -> True."""
    count = _to_int(raw.get(key))
    return None if count is None else count > 0


def first_non_blank(raw: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in ("null", "n/a", "na"):
            return text
    return None


def first_integer(raw: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _to_int(raw.get(key))
        if value is not None:
            return value
    return None


def resolve_battery_status(raw: Mapping[str, Any]) -> str:
    """Low / Reverse / Low & Reverse / OK — reads the statusbox + gateway ladders."""
    low = resolve_boolean(
        raw,
        "statusbox_battery_low",
        "system_status_statusbox_battery_low",
        "BATTERY LOW",
        "gatewayStatus_BATTERY LOW",
    )
    reverse = resolve_boolean(
        raw,
        "statusbox_battery_reverse",
        "system_status_statusbox_battery_reverse",
        "BATTERY REVERSE",
        "gatewayStatus_BATTERY REVERSE",
    )
    is_low = low is True
    is_reverse = reverse is True
    if is_low and is_reverse:
        return "Low & Reverse"
    if is_reverse:
        return "Reverse"
    if is_low:
        return "Low"
    return "OK"


def resolve_subsystem_fault(raw: Mapping[str, Any], target: str) -> bool | None:
    """Subsystem fault flag from the real fleet keys (ticketStatus_*/iasBasFasStatus_*/counts)."""
    if target == "ias":
        return resolve_boolean(
            raw,
            "ticketStatus_IAS_FAULT",
            "intrusion_alarm_system_fault",
            "INTRUSION ALARM SYSTEM FAULT",
            "iasBasFasStatus_INTRUSION ALARM SYSTEM FAULT",
        )
    if target == "bas":
        return resolve_from_count(raw, "BASfaultCOUNT")
    if target == "fas":
        return resolve_boolean(
            raw,
            "ticketStatus_FAS_FAULT",
            "fireAlarmSystem_fault",
            "FIRE ALARM SYSTEM FAULT",
            "fire_alarm_system_fault",
            "iasBasFasStatus_FIRE ALARM SYSTEM FAULT",
        )
    if target == "timeLock":
        return resolve_boolean(raw, "ticketStatus_TLS_TAMPER", "ticketStatus_TLS_OFF")
    if target == "accessControl":
        return resolve_boolean(raw, "ticketStatus_ACS_TAMPER", "ticketStatus_ACS_OFF")
    if target == "cctv":
        return resolve_boolean(
            raw,
            "HDD ERROR",
            "ticketStatus_HDD_ERROR",
            "ticketStatus_NVR_OFF",
            "cameraStatus_HDD ERROR",
        )
    return None


def resolve_subsystem_alarm(raw: Mapping[str, Any], target: str) -> bool | None:
    """Subsystem active-alarm flag; door-open subsystems fall back to a count key."""
    if target == "ias":
        return resolve_boolean(
            raw,
            "ticketStatus_IAS_ACTIVATE",
            "ticketStatus_IAS_INT_ACTIVATE",
            "INTRUSION ALARM SYSTEM ACTIVATE",
            "iasBasFasStatus_INTRUSION ALARM SYSTEM ACTIVATE",
        )
    if target == "bas":
        return None
    if target == "fas":
        return resolve_boolean(
            raw,
            "ticketStatus_FAS_ACTIVATE",
            "FIRE ALARM SYSTEM ACTIVATE",
            "iasBasFasStatus_FIRE ALARM SYSTEM ACTIVATE",
        )
    if target == "timeLock":
        door = resolve_boolean(raw, "ticketStatus_TLS_DOOR_OPEN")
        return door if door is not None else resolve_from_count(raw, "time_lock_door_open_count")
    if target == "accessControl":
        door = resolve_boolean(raw, "ticketStatus_ACS_DOOR_OPEN")
        return door if door is not None else resolve_from_count(raw, "access_control_door_open_count")
    if target == "cctv":
        return resolve_boolean(raw, "CAMERA DISCONNECT", "ticketStatus_CAM_DIS", "ticketStatus_CAM_TAMPER")
    return None
