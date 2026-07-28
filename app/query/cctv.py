"""Vendor-aware CCTV parsing — port of Java CctvHandler's NVR/recording/HDD logic.

Handles the multi-vendor key spellings (Hikvision / Dahua / CP Plus / rock) for NVR
device info, per-channel recording days, and HDD slot info. Pure functions over the
raw device dict (BranchSnapshot.raw_data). Single-branch only; fleet-wide recording
compliance (multi-snapshot) and camera-disconnect history are deferred.
"""

import json
from collections.abc import Mapping
from typing import Any

from app.query.answer_support import first_integer, first_non_blank

# Java default iotchatbot.cctv.recording-retention-days. ponytail: constant, not settings —
# thread a config value if the retention target ever needs per-deployment tuning.
RETENTION_DAYS = 90

# Per-channel recording-history keys across NVR vendors (Java CctvHandler.REC_KEYS).
#
# The `rock` family is DOTTED, not underscored. ThingsBoard stores rock as one JSON
# container and flatten.expand_containers addresses into it by path, so "rock.VIDEOdETAILS"
# is the name that resolves and "rock_VIDEOdETAILS" matches nothing on any device in the
# fleet. The underscore spellings are kept only as a fallback. The Hikvision_/Dahua_/
# CP_Plus_ keys are genuinely flat attribute names and stay as they are.
_REC_KEYS = (
    "rock.VIDEOdETAILS",
    "rockAI.VIDEOdETAILS",
    "rock_VIDEOdETAILS",
    "VIDEOdETAILS",
    "Hikvision_NVR_CameraRecInfo",
    "Dahua_NVR_CameraRecInfo",
    "CP_Plus_NVR_CameraRecInfo",
    "Hik_rock_NVR1_VIDEOdETAILS",
    "Hik_rock_NVR2_VIDEOdETAILS",
)

# Per-channel SD-card recording history (doc: "SD Recording Information").
_SD_REC_KEYS = ("rock.SdRecINFO", "rockAI.SdRecINFO", "rock_SdRecINFO")

_HDD_KEYS = ("rock.HddINFO", "rockAI.HddINFO", "rock_HddINFO", "Hikvision_NVR_HDDInfo")

# Per-camera inventory: model, IP, resolution, active status (doc: "Camera Information").
_CAMERA_KEYS = ("rock.CAMERAdETAILS", "rockAI.CAMERAdETAILS", "rock_CAMERAdETAILS")

# Every key these parsers read. The chat fetch unions this into the timeseries keys for
# cctv_* intents (which have no key_profile), so vendor JSON blobs stored as telemetry are
# imported — Java requests the same via keysFor. Keep in sync with the readers below.
CCTV_KEYS: frozenset[str] = frozenset(
    (
        *_REC_KEYS,
        *_SD_REC_KEYS,
        *_HDD_KEYS,
        *_CAMERA_KEYS,
        "Hikvision_NVR_model",
        "rock.model",
        "rockAI.model",
        "Dahua_NVR_model",
        "nvr_brand",
        "rock.NoOfHDDSlots",
        "Hikvision_NVR_NoOfHDDSlots1",
        "Dahua_NVR_NoOfHDDSlots",
        "count_HDD",
        "rock.capacity",
        "rock.freeSpace",
        "Hikvision_NVR_capacity1",
        "Dahua_NVR_capacity",
        "Video Resolution",
        "Hikvision_NVR_Resolutions",
        "Dahua_NVR_Resolutions",
        "CP_Plus_NVR_Resolutions",
        "HDD ERROR",
        "ticketStatus_HDD_ERROR",
        "cameraStatus_HDD ERROR",
        "hddStatus",
    )
)


def _first_list(raw: Mapping[str, Any], keys: tuple[str, ...]) -> list[Any] | None:
    """First key among vendor spellings that holds a non-empty list."""
    for key in keys:
        arr = _as_list(raw.get(key))
        if arr:
            return arr
    return None


def _as_list(value: object) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _first_text(entry: Mapping[str, Any], *fields: str) -> str:
    """First present field's text among alternative vendor spellings; 'N/A' if none."""
    for field in fields:
        if field in entry:
            value = entry[field]
            return "N/A" if value is None else str(value)
    return "N/A"


def nvr_vendor(raw: Mapping[str, Any], model: str | None) -> str | None:
    """Reported nvr_brand, else infer from model prefix. dexter_config brand is the
    integrator (e.g. SEPLE), not the NVR make, so it is deliberately not used."""
    brand = first_non_blank(raw, "nvr_brand")
    if brand is not None:
        return brand
    if model is None:
        return None
    m = model.upper()
    if m.startswith(("DS-", "IDS-")):
        return "Hikvision"
    if m.startswith(("DH", "XVR", "NVR4")):
        return "Dahua"
    if m.startswith(("CP-", "CP_")):
        return "CPPLUS"
    return None


def _positive_double(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        d = float(value.strip())
    except ValueError:
        return None
    return d if d > 0 else None


def device_info(raw: Mapping[str, Any]) -> dict[str, Any]:
    """NVR inventory: vendor, model, HDD slot count, storage TB, resolution. Empty when unknown."""
    model = first_non_blank(
        raw, "Hikvision_NVR_model", "rock.model", "rockAI.model", "rock_model", "Dahua_NVR_model"
    )
    info: dict[str, Any] = {}
    vendor = nvr_vendor(raw, model)
    if vendor is not None:
        info["vendor"] = vendor
    if model is not None:
        info["model"] = model
    hdd_slots = first_integer(
        raw,
        "rock.NoOfHDDSlots",
        "rock_NoOfHDDSlots",
        "Hikvision_NVR_NoOfHDDSlots1",
        "Dahua_NVR_NoOfHDDSlots",
        "count_HDD",
    )
    if hdd_slots is not None:
        info["hdd_slots"] = hdd_slots
    storage = _positive_double(
        first_non_blank(
            raw, "rock.capacity", "rock_capacity", "Hikvision_NVR_capacity1", "Dahua_NVR_capacity"
        )
    )
    if storage is not None:
        info["storage_tb"] = storage
    resolution = first_non_blank(raw, "Video Resolution", "Hikvision_NVR_Resolutions", "Dahua_NVR_Resolutions", "CP_Plus_NVR_Resolutions")
    if resolution is not None:
        info["resolution"] = resolution
    return info


def hdd_info(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    """Per-slot HDD info. Reads both Hikvision (HDDSlots/HDDcapacity/HDDfreeSpace) and
    Dahua/XVR (HDDSlot/HDDCapacity/HDDFreeSpace) field schemas."""
    arr = _first_list(raw, _HDD_KEYS)
    if arr is None:
        return []
    slots: list[dict[str, str]] = []
    for entry in arr:
        if not isinstance(entry, dict):
            continue
        slots.append(
            {
                "slot": _first_text(entry, "HDDSlots", "HDDSlot"),
                "status": str(entry.get("HDDStatus", "N/A")),
                "capacity_tb": _first_text(entry, "HDDcapacity", "HDDCapacity"),
                "free_tb": _first_text(entry, "HDDfreeSpace", "HDDFreeSpace"),
            }
        )
    return slots


def parse_recordings(raw: Mapping[str, Any]) -> dict[str, int]:
    """Per-channel recorded days across every vendor RecInfo array. Vendors name the days
    field total_recording_days (CP Plus/Dahua) or total_duration (Hikvision/rock) and the
    channel channel_no/channel/camera_id. Keyed by channel and kept at the max seen, so the
    same camera reported under two NVR keys isn't double-counted."""
    by_channel: dict[str, int] = {}
    for key in _REC_KEYS:
        arr = _as_list(raw.get(key))
        if arr is None:
            continue
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            if "channel_no" in entry:
                channel = str(entry["channel_no"])
            elif "channel" in entry:
                channel = str(entry["channel"])
            else:
                channel = str(entry.get("camera_id", ""))
            channel = channel.strip()
            if channel == "" or channel.lower() == "n/a":
                continue
            if "total_recording_days" in entry:
                days = _coerce_int(entry["total_recording_days"], 0)
            else:
                days = _coerce_int(entry.get("total_duration"), 0)
            if channel not in by_channel or days > by_channel[channel]:
                by_channel[channel] = days
    return by_channel


def recording_summary(raw: Mapping[str, Any], retention_days: int = RETENTION_DAYS) -> dict[str, Any]:
    """Single-branch recording compliance vs the retention target."""
    cams = parse_recordings(raw)
    if not cams:
        return {"available": False}
    compliant = non_compliant = zero = 0
    zero_channels: list[str] = []
    days_values = list(cams.values())
    for channel, days in cams.items():
        if days <= 0:
            zero += 1
            zero_channels.append(channel)
        if days >= retention_days:
            compliant += 1
        else:
            non_compliant += 1
    return {
        "available": True,
        "total": len(cams),
        "compliant": compliant,
        "non_compliant": non_compliant,
        "zero": zero,
        "zero_channels": zero_channels,
        "min_days": min(days_values),
        "max_days": max(days_values),
        "retention_days": retention_days,
    }
