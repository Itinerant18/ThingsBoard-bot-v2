"""Computed answers from docs/Telimetry-Attribute-key.md.

Several rows in that document are not a key lookup but a small computation — total
HDD capacity is a sum over rock.HddINFO, total cameras is the length of
rock.VIDEOdETAILS, HDD error count filters on HDDStatus. This module is the Python
of the JavaScript written in the doc's Key column.

Shapes are taken from the live fleet, not assumed:
  rock.HddINFO      [{"HDDSlot": "Slot NA", "HDDStatus": "Idle",
                      "HDDCapacity": "0.00", "HDDFreeSpace": "0.00"}, ...]
  rock.CAMERAdETAILS  a list that CONTAINS NULLS for unpopulated channels
  rock.VIDEOdETAILS   often []
  cameraTamperCount   an object ({}), not a number

Every helper tolerates a missing key, a JSON string, an empty list and null
entries, because all four occur in production.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

_MISSING = {"", "n/a", "na", "null", "none", "-"}


def _as_list(value: Any) -> list[Any]:
    """A list from an already-parsed value or a JSON string; [] otherwise."""
    if isinstance(value, str):
        text = value.strip()
        if not text.startswith("["):
            return []
        try:
            value = json.loads(text)
        except ValueError:
            return []
    return [item for item in value if item is not None] if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        text = value.strip()
        if not text.startswith("{"):
            return {}
        try:
            value = json.loads(text)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in _MISSING):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _MISSING else text


# --- CCTV --------------------------------------------------------------------


def hdd_rows(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """doc "HDD Information": slot, status, capacity, free space per disk."""
    rows = []
    for entry in _as_list(raw.get("rock.HddINFO")):
        if not isinstance(entry, Mapping):
            continue
        # Two field schemas in the fleet: Dahua/XVR spells them HDDSlot/HDDCapacity/
        # HDDFreeSpace, Hikvision HDDSlots/HDDcapacity/HDDfreeSpace. Reading only one
        # returned a null capacity for every Hikvision NVR, which is most of them.
        rows.append(
            {
                "slot": _text(entry.get("HDDSlot") or entry.get("HDDSlots")),
                "status": _text(entry.get("HDDStatus")),
                "capacity": _number(entry.get("HDDCapacity") or entry.get("HDDcapacity")),
                "free_space": _number(entry.get("HDDFreeSpace") or entry.get("HDDfreeSpace")),
            }
        )
    return rows


def hdd_total_capacity(raw: Mapping[str, Any]) -> float | None:
    """doc "Cctv storage status": sum of HDDCapacity. None when no disk reports one,
    which is different from a genuine 0."""
    values = [row["capacity"] for row in hdd_rows(raw) if row["capacity"] is not None]
    return sum(values) if values else None


def hdd_free_space(raw: Mapping[str, Any]) -> float | None:
    values = [row["free_space"] for row in hdd_rows(raw) if row["free_space"] is not None]
    return sum(values) if values else None


def hdd_error_count(raw: Mapping[str, Any]) -> int:
    """doc "Cctv hdd error count": HDDStatus == "error", case-insensitive."""
    return sum(1 for row in hdd_rows(raw) if (row["status"] or "").lower() == "error")


def camera_count(raw: Mapping[str, Any]) -> int:
    """doc "Total Camera": rock.VIDEOdETAILS.length.

    Falls back to CAMERAdETAILS because VIDEOdETAILS is empty on many devices while
    the camera inventory is populated.
    """
    videos = _as_list(raw.get("rock.VIDEOdETAILS"))
    return len(videos) if videos else len(_as_list(raw.get("rock.CAMERAdETAILS")))


def camera_rows(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """doc "Camera Information". Null entries are dropped by _as_list — the fleet
    pads this array with nulls for unpopulated channels."""
    rows = []
    for entry in _as_list(raw.get("rock.CAMERAdETAILS")):
        if not isinstance(entry, Mapping):
            continue
        rows.append(
            {
                "channel": _text(entry.get("channel_no")) or _text(entry.get("id")),
                "name": _text(entry.get("Channel Name")),
                "manufacturer": _text(entry.get("manufacturer")),
                "model": _text(entry.get("model")),
                "serial_number": _text(entry.get("serialNumber")),
                "resolution": _text(entry.get("resolution")),
                "fps": _text(entry.get("fps")),
                "ip_address": _text(entry.get("IP Address")),
                "status": _text(entry.get("status")) or _text(entry.get("Active Status")),
            }
        )
    return rows


def recording_rows(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """doc "NVR Recording Information"."""
    rows = []
    for entry in _as_list(raw.get("rock.VIDEOdETAILS")):
        if not isinstance(entry, Mapping):
            continue
        rows.append(
            {
                "camera": _text(entry.get("cameraName")),
                "ip": _text(entry.get("cameraIP")),
                "start": _text(entry.get("start_time")),
                "end": _text(entry.get("end_time")),
                "days": _text(entry.get("total_duration")),
            }
        )
    return rows


def sd_recording_rows(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """doc "SD Recording Information"."""
    rows = []
    for entry in _as_list(raw.get("rock.SdRecINFO")):
        if not isinstance(entry, Mapping):
            continue
        rows.append(
            {
                "channel": _text(entry.get("channel_no")),
                "name": _text(entry.get("channel_name")),
                "ip": _text(entry.get("cameraIP")),
                "start": _text(entry.get("start_time")),
                "end": _text(entry.get("end_time")),
                "days": _text(entry.get("total_recording_days")),
            }
        )
    return rows


def cctv_device_info(raw: Mapping[str, Any]) -> dict[str, Any]:
    """doc "Cctv about device"."""
    fields = {
        "system_time": "rock.Time",
        "system_date": "rock.dexter_date",
        "last_sync": "rock.syncTimeDate",
        "device_type": "rock.deviceType",
        "manufacturer": "rock.manufacturer",
        "model": "rock.model",
        "serial_number": "rock.serialNumber",
        "firmware_version": "rock.firmwareVersion",
        "mf_date": "rock.mfDate",
    }
    return {label: _text(raw.get(key)) for label, key in fields.items()}


def _count_like(value: Any) -> int | None:
    """cameraTamperCount / cameraDisconnectCount arrive as an OBJECT in production
    ({} when clear), though the name implies a number. Accept both."""
    number = _number(value)
    if number is not None:
        return int(number)
    mapping = _as_dict(value)
    return len(mapping) if isinstance(value, Mapping | str) else None


def camera_tamper_count(raw: Mapping[str, Any]) -> int | None:
    return _count_like(raw.get("cameraTamperCount"))


def camera_disconnect_count(raw: Mapping[str, Any]) -> int | None:
    return _count_like(raw.get("cameraDisconnectCount"))


# --- network -----------------------------------------------------------------


def network_status(raw: Mapping[str, Any]) -> dict[str, Any]:
    """doc: if statusbox_network exists and is non-empty -> On, operator = its value;
    otherwise Off with operator "-"."""
    operator = _text(raw.get("statusbox_network")) or _text(raw.get("system_status.statusbox_network"))
    return {"status": "On" if operator else "Off", "operator": operator or "-"}


def connected_devices(raw: Mapping[str, Any]) -> int | None:
    value = _number(
        raw.get("statusbox_no_of_connected_device")
        if raw.get("statusbox_no_of_connected_device") is not None
        else raw.get("system_status.statusbox_no_of_connected_device")
    )
    return None if value is None else int(value)


def sos_status(raw: Mapping[str, Any]) -> str | None:
    value = _text(raw.get("statusbox_sos_status"))
    if value is None:
        return None
    return "Active" if value.lower() in {"true", "1", "on", "active"} else "Clear"


# --- BAS panel ---------------------------------------------------------------


def bas_panel(raw: Mapping[str, Any]) -> dict[str, Any]:
    """doc basSystemIntegration.basMainInfo — heartbeat / panelState / panelMode."""
    info = _as_dict(raw.get("basSystemIntegration.basMainInfo"))
    return {
        "heartbeat": _text(info.get("heartbeat")),
        "panel_state": _text(info.get("panelState")),
        "panel_mode": _text(info.get("panelMode")),
        "zones_supported": _number(info.get("zoneSupported")),
    }


def bas_power(raw: Mapping[str, Any]) -> dict[str, Any]:
    info = _as_dict(raw.get("basSystemIntegration.basPowerStatus"))
    return {
        "system_voltage": _text(info.get("systemVoltage")),
        "battery_voltage": _text(info.get("batteryVoltage")),
        "system_current": _text(info.get("systemCurrent")),
        "battery_current": _text(info.get("batteryCurrent")),
        "battery_status": _text(info.get("batteryStatus")),
        "mains_status": _text(info.get("mainStatus")),
    }


def bas_device(raw: Mapping[str, Any]) -> dict[str, Any]:
    info = _as_dict(raw.get("basSystemIntegration.basAboutDevice"))
    return {
        "panel_ip": _text(info.get("panelIp")),
        "model": _text(info.get("model")),
        "firmware_version": _text(info.get("firmwareVersion")),
        "last_updated": _text(info.get("lastUpdated")),
    }


def bas_zones(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """doc "Bas zone information"."""
    rows = []
    for entry in _as_list(raw.get("basSystemIntegration.zoneInfo")):
        if not isinstance(entry, Mapping):
            continue
        rows.append(
            {
                "zone": _text(entry.get("zoneName")),
                "area_state": _text(entry.get("areaStates")),
                "bell": _text(entry.get("bell")),
                "event": _text(entry.get("zoneEvent")),
                "timestamp": _text(entry.get("timeStamp")),
            }
        )
    return rows


# --- doors -------------------------------------------------------------------


def door_status(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "time_lock": _text(raw.get("timeLockDoor")),
        "access_control": _text(raw.get("accessControlDoor")),
    }


def summarize(label: str, rows: Sequence[Mapping[str, Any]], limit: int = 5) -> str:
    """One-line-per-row rendering, truncated so a 16-camera NVR does not flood chat."""
    if not rows:
        return f"No {label} is being reported for this device."
    shown = [
        ", ".join(f"{k}: {v}" for k, v in row.items() if v not in (None, ""))
        for row in rows[:limit]
    ]
    more = f" (+{len(rows) - limit} more)" if len(rows) > limit else ""
    return f"{label} ({len(rows)}):\n- " + "\n- ".join(shown) + more
