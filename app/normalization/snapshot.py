"""BranchSnapshot mapping — direct port of Java BranchSnapshotMapper + domain.

Contract: docs/thingsboard-key-map.md. Deterministic: same raw device dict ->
same snapshot. This is pure normalization of ONE device's raw attribute/telemetry
dict; tenant scoping is enforced upstream (app/hierarchy/scope.py), so no
customer_prefix lives here.

ponytail: dropped from the Java port (no consumer in v2 yet, add when the
answer/aggregation layer lands):
  - BranchSnapshot.isOperationalBranch (DEMO/TEST/… filter) -> query layer
  - AlertSummary severity counts + topAlarms (populated from TB Alarm API on demand)
  - CCTV offline-channel range formatting -> answer-formatting slice
  - intent->key profiles (§2) -> intent-extraction slice
"""

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from app.normalization.resolver import (
    resolve_ac_voltage,
    resolve_battery_voltage,
    resolve_gateway_state,
    resolve_mains_on,
    resolve_subsystem_state,
    resolve_system_current,
)
from app.normalization.values import NormalizedState, to_bool, to_double, to_int


@dataclass
class BranchIdentity:
    branch_name: str | None
    technical_id: str | None
    device_id: str | None
    branch_id: str | None
    aliases: list[str] = field(default_factory=list)


@dataclass
class GatewayStatus:
    state: NormalizedState
    health: str | None
    active: bool | None
    source_field_used: str | None


@dataclass
class PowerStatus:
    battery_voltage: float | None
    battery_voltage_source: str | None
    ac_voltage: float | None
    system_current: float | None
    battery_low: bool | None
    mains_on: bool | None


@dataclass
class SubsystemStatus:
    system_name: str
    state: NormalizedState
    installed: bool
    raw_value: str | None
    health: str | None
    source_field_used: str | None
    power_status: str | None
    system_status: str | None
    log_status: str | None
    health_status: str | None


@dataclass
class BranchSubsystems:
    cctv: SubsystemStatus
    ias: SubsystemStatus
    bas: SubsystemStatus
    fas: SubsystemStatus
    time_lock: SubsystemStatus
    access_control: SubsystemStatus


@dataclass
class CctvStatus:
    state: NormalizedState
    camera_count: int | None
    online_camera_count: int | None
    has_disconnect: bool
    has_tamper: bool
    hdd_status: str | None


@dataclass
class AlertSummary:
    alarm_count: int
    error_count: int
    nvr_off: bool
    hdd_error: bool
    camera_disconnect: bool
    camera_tamper: bool
    intrusion_activate: bool
    fire_activate: bool
    power_off: bool
    battery_low: bool


@dataclass
class HardwareHealth:
    cpu: float | None
    memory: float | None
    disk: float | None
    temperature: float | None


@dataclass
class BranchSnapshot:
    identity: BranchIdentity
    gateway: GatewayStatus
    power: PowerStatus
    subsystems: BranchSubsystems
    cctv: CctvStatus
    alerts: AlertSummary
    hardware: HardwareHealth
    warnings: list[str] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (NormalizedState is a str-Enum, so it dumps clean)."""
        return asdict(self)


# --- small helpers (ports of the Java private methods) -----------------------


def _string_value(value: object) -> str | None:
    return None if value is None else str(value)


def _choose(raw: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _string_value(raw.get(key))
        if value is not None and value.strip() != "" and value.lower() != "null":
            return value
    return None


def _boolean_flag(raw: Mapping[str, Any], key: str) -> bool:
    return to_bool(_string_value(raw.get(key))) is True


def _as_json(value: object) -> Any:
    """Return a list/dict from an already-parsed value or a JSON string, else None.

    v2 gets these containers from httpx .json(), so they may already be parsed;
    the Java bot always saw flattened JSON *strings*. Accept both shapes.
    """
    if isinstance(value, list | dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("[", "{")):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return None
    return None


# Mapper's nested-container search (§10). NOTE: different parent list + different
# return semantics from the resolver's _find_in_nested_json — kept separate.
_JSON_PARENTS = ("rock", "dexter_config", "cameraStatus", "gatewayStatus", "ticketStatus", "rockAI")


def _find_container(raw: Mapping[str, Any], *candidate_keys: str) -> Any:
    for key in candidate_keys:
        parsed = _as_json(raw.get(key))
        if parsed is not None:
            return parsed
    for parent_key in _JSON_PARENTS:
        parent = _as_json(raw.get(parent_key))
        if isinstance(parent, dict):
            for cand in candidate_keys:
                if cand in parent:
                    child = _as_json(parent[cand])
                    if child is not None:
                        return child
    return None


def _entry_int(entry: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return int(entry[key])
    except (KeyError, TypeError, ValueError):
        return default


def _add_aliases(alias_set: set[str], value: str | None) -> None:
    if value is None or value.strip() == "":
        return
    t = value.strip()
    alias_set.add(t)
    alias_set.add(t.replace("BOI-", ""))
    alias_set.add(t.replace("BOI-", "").replace("-", " "))
    alias_set.add(t.replace("BRANCH ", ""))
    alias_set.add(t.replace("BRANCH ", "").replace("-", " "))
    alias_set.add(t.replace(" ", ""))


# --- section builders --------------------------------------------------------


def _build_identity(raw: Mapping[str, Any]) -> BranchIdentity:
    branch_name = _choose(raw, "branchName", "formattedBranchName", "device_name", "deviceName")
    if branch_name is not None:
        branch_name = branch_name.upper().strip()
    technical_id = _choose(raw, "device_name", "deviceName", "formattedBranchName", "branchName")

    alias_set: set[str] = set()
    _add_aliases(alias_set, branch_name)
    _add_aliases(alias_set, technical_id)
    _add_aliases(alias_set, _string_value(raw.get("deviceName")))
    _add_aliases(alias_set, _string_value(raw.get("formattedBranchName")))
    _add_aliases(alias_set, _string_value(raw.get("branchName")))

    return BranchIdentity(
        branch_name=branch_name,
        technical_id=technical_id,
        device_id=_string_value(raw.get("device_id")),
        branch_id=_string_value(raw.get("branch_id")),
        aliases=sorted(alias_set),  # Java HashSet order is undefined; sort for determinism.
    )


def _build_gateway(raw: Mapping[str, Any], warnings: list[str]) -> GatewayStatus:
    resolved = resolve_gateway_state(raw)
    if "gateway" in raw and "status" in raw:
        gateway = _string_value(raw.get("gateway"))
        status = _string_value(raw.get("status"))
        if gateway is not None and status is not None and gateway.lower() != status.lower():
            warnings.append(f"Gateway/status conflict: gateway={gateway}, status={status}")
    return GatewayStatus(
        state=resolved.state,
        health=_string_value(raw.get("gwHealth")),
        active=to_bool(_string_value(raw.get("active"))),
        source_field_used=resolved.source_field,
    )


def _build_power(raw: Mapping[str, Any], warnings: list[str]) -> PowerStatus:
    battery = resolve_battery_voltage(raw)
    ac = resolve_ac_voltage(raw)
    current = resolve_system_current(raw)

    battery_status_v = to_double(raw.get("battery_status_battery_voltage"))
    gateway_v = to_double(raw.get("gatewayStatus_battery_voltage"))
    if battery_status_v is not None and gateway_v is not None and battery_status_v != gateway_v:
        warnings.append(
            f"Battery voltage conflict: battery_status_battery_voltage={battery_status_v}, "
            f"gatewayStatus_battery_voltage={gateway_v}"
        )

    return PowerStatus(
        battery_voltage=battery.value,
        battery_voltage_source=battery.source_field,
        ac_voltage=ac.value,
        system_current=current.value,
        # Two reads of "BATTERY LOW" on purpose: power keeps bool|None, alerts coerces to bool.
        battery_low=to_bool(_string_value(raw.get("BATTERY LOW"))),
        mains_on=resolve_mains_on(raw),
    )


def _subsystem_health(raw: Mapping[str, Any], system_name: str) -> str | None:
    match system_name:
        case "Time Lock":
            return _string_value(raw.get("timeLockHealth"))
        case "Access Control":
            return _string_value(raw.get("accessControlStatus"))
        case "FAS":
            return _choose(raw, "fasStatus", "fireAlarmStatus")
        case "IAS":
            return _choose(raw, "iasStatus", "ias_status")
        case "CCTV":
            return _choose(raw, "cctvStatus", "cameraLinkStatus")
        case "BAS":
            return _string_value(raw.get("basStatus"))
        case _:
            return None


def _build_subsystem(
    raw: Mapping[str, Any], system_name: str, primary_field: str, *fallbacks: str
) -> SubsystemStatus:
    resolved = resolve_subsystem_state(raw, primary_field, *fallbacks)
    state = resolved.state
    # installed is computed from the ORIGINAL state and never recomputed after the
    # N/A override below — so cctv_sts="N/A" ends NOT_INSTALLED + installed=False.
    installed = state not in (NormalizedState.NOT_INSTALLED, NormalizedState.UNKNOWN)
    if not installed:
        primary_value = _string_value(raw.get(primary_field))
        if primary_value is not None and primary_value.lower() in ("n/a", "null"):
            state = NormalizedState.NOT_INSTALLED

    prefix = primary_field.removesuffix("_sts")

    return SubsystemStatus(
        system_name=system_name,
        state=state,
        installed=installed,
        raw_value=resolved.raw_value,
        health=_subsystem_health(raw, system_name),
        source_field_used=resolved.source_field,
        power_status=_string_value(raw.get(f"{prefix}_powerStatus")),
        system_status=_string_value(raw.get(f"{prefix}_systemStatus")),
        log_status=_string_value(raw.get(f"{prefix}_logStatus")),
        health_status=_string_value(raw.get(f"{prefix}_healthStatus")),
    )


def _camera_online(status: str | None) -> bool:
    # §6 step 1: literal match, NOT to_state — no status key at all counts as online.
    return status is None or status.strip().lower() in {"active", "online", "on", "1", "true"}


def _build_cctv(raw: Mapping[str, Any], cctv_subsystem: SubsystemStatus) -> CctvStatus:
    total: int | None = None
    online: int | None = None

    # 1. CAMERAdETAILS array.
    cameras = _find_container(
        raw, "rock_CAMERAdETAILS", "CAMERAdETAILS", "CAMERA_DETAILS", "CAMERADETAILS"
    )
    if isinstance(cameras, list) and cameras:
        total_count = 0
        online_count = 0
        for camera in cameras:
            if not isinstance(camera, dict):
                continue
            total_count += 1
            status: str | None = None
            for k in ("cameraStatus", "status", "Active Status", "active_status", "camera_status"):
                if k in camera:
                    status = str(camera[k])
                    break
            if _camera_online(status):
                online_count += 1
        if total_count > 0:
            total = total_count
            online = online_count

    # 2. Recording-info array — wins when it exceeds step 1.
    rec_info = _find_container(
        raw,
        "rock_VIDEOdETAILS",
        "VIDEOdETAILS",
        "rock_SdRecINFO",
        "SdRecINFO",
        "Hikvision_NVR_CameraRecInfo",
        "Dahua_NVR_CameraRecInfo",
        "CP_Plus_NVR_CameraRecInfo",
        "CameraRecInfo",
    )
    if isinstance(rec_info, list) and rec_info:
        total_count = len(rec_info)
        online_count = 0
        for entry in rec_info:
            if isinstance(entry, dict):
                duration = _entry_int(entry, "total_duration", _entry_int(entry, "total_recording_days", 0))
                start_time = str(entry.get("start_time", "N/A"))
                if duration > 0 or (start_time.lower() != "n/a" and start_time.strip() != ""):
                    online_count += 1
        if total is None or total_count > total:
            total = total_count
            online = online_count if online_count > 0 else total_count

    # 3. dexter_config camera_ip.
    if total is None or total <= 0:
        dexter = _find_container(raw, "dexter_config")
        if isinstance(dexter, dict):
            integration = dexter.get("integration")
            if isinstance(integration, list):
                for nvr in integration:
                    if isinstance(nvr, dict):
                        camera_ips = nvr.get("camera_ip")
                        if isinstance(camera_ips, list) and camera_ips:
                            total = len(camera_ips)
                            online = len(camera_ips)
                            break

    # 4. CAMERA DISCONNECT CH <n> keys.
    if total is None or total <= 0:
        max_ch = 0
        disconnected = 0
        for key in raw:
            if "CAMERA DISCONNECT CH " in key.upper():
                match = re.search(r"(?i)CAMERA DISCONNECT CH\s*(\d+)", key)
                if not match:
                    continue
                ch = int(match.group(1))
                max_ch = max(max_ch, ch)
                val = str(raw[key]).strip().lower()
                if val in ("true", "1", "yes"):
                    disconnected += 1
        if max_ch > 0:
            total = max_ch
            online = max(0, max_ch - disconnected)

    # 5. Direct count attributes.
    if total is None or total <= 0:
        count = to_int(
            _choose(
                raw,
                "count_camera",
                "no_of_connected_cctv",
                "cctv_count",
                "no_of_cameras",
                "total_cameras",
                "Hikvision_NVR_NoOfCameras",
            ),
            0,
        )
        if count > 0:
            total = count
            online = count

    hdd_status: str | None = None
    for k in ("rock_HddINFO", "HddINFO", "HDD_INFO"):
        hdd = _as_json(raw.get(k))
        if isinstance(hdd, list) and hdd and isinstance(hdd[0], dict):
            raw_hdd = hdd[0].get("HDDStatus")
            hdd_status = None if raw_hdd is None else str(raw_hdd)
            break

    return CctvStatus(
        state=cctv_subsystem.state,
        camera_count=total,
        online_camera_count=online,
        has_disconnect=_boolean_flag(raw, "CAMERA DISCONNECT"),
        has_tamper=_boolean_flag(raw, "CAMERA TAMPER"),
        hdd_status=hdd_status,
    )


def _build_alerts(raw: Mapping[str, Any]) -> AlertSummary:
    return AlertSummary(
        alarm_count=to_int(raw.get("alarmCount"), 0),
        error_count=to_int(raw.get("errorCount"), 0),
        nvr_off=_boolean_flag(raw, "DVR/NVR OFF") or _boolean_flag(raw, "ticketStatus_NVR_OFF"),
        hdd_error=_boolean_flag(raw, "HDD ERROR"),
        camera_disconnect=_boolean_flag(raw, "CAMERA DISCONNECT"),
        camera_tamper=_boolean_flag(raw, "CAMERA TAMPER"),
        intrusion_activate=_boolean_flag(raw, "INTRUSION ALARM SYSTEM ACTIVATE"),
        fire_activate=_boolean_flag(raw, "FIRE ALARM SYSTEM ACTIVATE"),
        power_off=_boolean_flag(raw, "POWER OFF"),
        battery_low=_boolean_flag(raw, "BATTERY LOW"),
    )


def _build_hardware(raw: Mapping[str, Any]) -> HardwareHealth:
    return HardwareHealth(
        cpu=to_double(raw.get("cpu")),
        memory=to_double(raw.get("memory")),
        disk=to_double(raw.get("disk")),
        temperature=to_double(raw.get("temperature")),
    )


def build_snapshot(raw: Mapping[str, Any]) -> BranchSnapshot:
    """Map one device's raw attribute/telemetry dict to a canonical BranchSnapshot."""
    warnings: list[str] = []

    identity = _build_identity(raw)
    gateway = _build_gateway(raw, warnings)
    power = _build_power(raw, warnings)
    subsystems = BranchSubsystems(
        cctv=_build_subsystem(
            raw,
            "CCTV",
            "cctv_sts",
            "cameraStatus_cctvStatus",
            "cctvStatus",
            "cctv_status",
            "cameraLinkStatus",
            "cctv_state",
            "rock_cctv_status",
        ),
        ias=_build_subsystem(raw, "IAS", "ias_sts"),
        bas=_build_subsystem(raw, "BAS", "bas_sts"),
        fas=_build_subsystem(raw, "FAS", "fas_sts"),
        time_lock=_build_subsystem(raw, "Time Lock", "timeLock_sts"),
        access_control=_build_subsystem(raw, "Access Control", "accessControl_sts"),
    )

    cctv = _build_cctv(raw, subsystems.cctv)

    # §3 dynamic CCTV fallback: only when explicit cctv_sts is UNKNOWN/absent.
    if subsystems.cctv.state == NormalizedState.UNKNOWN:
        inferred = NormalizedState.UNKNOWN
        if cctv.online_camera_count is not None and cctv.online_camera_count > 0:
            inferred = NormalizedState.ONLINE
        elif gateway.state == NormalizedState.OFFLINE:
            inferred = NormalizedState.OFFLINE
        if inferred != NormalizedState.UNKNOWN:
            subsystems.cctv.state = inferred
            subsystems.cctv.installed = True

    return BranchSnapshot(
        identity=identity,
        gateway=gateway,
        power=power,
        subsystems=subsystems,
        cctv=cctv,
        alerts=_build_alerts(raw),
        hardware=_build_hardware(raw),
        warnings=warnings,
        raw_data=dict(raw),
    )
