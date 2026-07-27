"""Intent -> telemetry keys — direct port of Java IntentKeyProfileRegistry.

Contract: docs/thingsboard-key-map.md §2. These are the exact ThingsBoard
telemetry/attribute keys most relevant to a given metric intent (some contain
spaces, e.g. "BATTERY LOW"); intents with no profile return []. Keys are 1:1 with
the Java QueryIntent enum names, lowercased. The chat fetch requests the intent's
profile UNION the answer-layer ladder keys as the explicit timeseries keys list.
"""

_BATTERY_VOLTAGE = [
    "battery_status_battery_voltage",
    "gatewayStatus_battery_voltage",
    "BATTERY LOW",
]
_SUBSYSTEM = [
    "iasStatus",
    "basStatus",
    "fasStatus",
    "timeLock",
    "timeLockHealth",
    "accessControlStatus",
    "ticketStatus_IAS_FAULT",
    "ticketStatus_FAS_FAULT",
    "BASfaultCOUNT",
    "ticketStatus_TLS_TAMPER",
    "ticketStatus_ACS_TAMPER",
    "ticketStatus_IAS_ACTIVATE",
    "ticketStatus_FAS_ACTIVATE",
    "ticketStatus_TLS_DOOR_OPEN",
    "ticketStatus_ACS_DOOR_OPEN",
]
_CCTV_RECORDING = [
    "rock_VIDEOdETAILS",
    "VIDEOdETAILS",
    "Hikvision_NVR_CameraRecInfo",
    "Dahua_NVR_CameraRecInfo",
    "CP_Plus_NVR_CameraRecInfo",
    "Hik_rock_NVR1_VIDEOdETAILS",
    "Hik_rock_NVR2_VIDEOdETAILS",
]

INTENT_KEYS: dict[str, list[str]] = {
    "gateway_status": ["gateway", "status", "gatewayStatus"],
    "battery_voltage": _BATTERY_VOLTAGE,
    "battery_health": _BATTERY_VOLTAGE,
    "battery_low_status": ["BATTERY LOW", "gatewayStatus_BATTERY LOW", "ticketStatus_BATTERY_LOW"],
    "power_status": [
        "battery_status_battery_voltage",
        "gatewayStatus_battery_voltage",
        "BATTERY LOW",
        "gatewayStatus_ac_voltage",
        "ac_status_ac_voltage",
        "ac_result",
        "MAINS ON",
        "gatewayStatus_MAINS ON",
        "system_status_statusbox_mains_on",
    ],
    "ac_voltage": ["gatewayStatus_ac_voltage", "ac_status_ac_voltage", "ac_result"],
    "system_current": ["gatewayStatus_system_current", "current_status_system_current", "cur_result"],
    "network_status": ["gatewayStatus_NETWORK", "system_status_statusbox_network", "networkOperator"],
    "cctv_status": [
        "cctv_sts",
        "cameraStatus_cctvStatus",
        "cctvStatus",
        "cctv_status",
        "cameraLinkStatus",
        "rock_CAMERAdETAILS",
        "CAMERAdETAILS",
        "Hikvision_NVR_CameraRecInfo",
        "rock_VIDEOdETAILS",
        "hikvision_camera_status",
    ],
    "cctv_hdd_error_status": ["HDD ERROR", "ticketStatus_HDD_ERROR", "cameraStatus_HDD ERROR", "hddStatus"],
    "cctv_hdd_info": ["rock_HddINFO", "hddStatus"],
    "cctv_recording_info": _CCTV_RECORDING,
    "cctv_recording_compliance": _CCTV_RECORDING,
    "subsystem_status": _SUBSYSTEM,
    "subsystem_fault_status": _SUBSYSTEM,
    "subsystem_alarm_status": _SUBSYSTEM,
    "device_hardware": [
        "cpu",
        "memory",
        "disk",
        "temperature",
        "target_sw_version",
        "sw_state",
        "sw_version",
    ],
    "fault_reason": ["Device_Issue"],
    "door_status": ["timeLockDoor", "accessControlDoor"],
    "access_control_user_count": [
        "accessControlTotalUsers",
        "totalUsers",
        "registeredUsers",
        "accessControlUserCount",
    ],
    "access_control_device_info": [
        "accessControlModel",
        "biometricModel",
        "acsModel",
        "accessControlFirmware",
        "biometricFirmware",
        "accessControlFirmwareVersion",
        "accessControlIp",
        "biometricIp",
        "accessControlIP",
    ],
}


def keys_for(intent: str) -> list[str]:
    """Telemetry keys to request for an intent; [] when the intent has no profile."""
    return list(INTENT_KEYS.get(intent, []))
