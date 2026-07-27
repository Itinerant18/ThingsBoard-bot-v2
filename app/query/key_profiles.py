"""Intent -> device keys, from docs/Telimetry-Attribute-key.md.

That document is the SOURCE OF TRUTH. docs/thingsboard-key-map.md and
docs/real-device-keys.md are SUPERSEDED: the profiles derived from them named keys
that exist on no device — 69 of 120 matched nothing across all 128 production
devices — so battery voltage, AC voltage, system current and CCTV status were
unanswerable while the values sat in ThingsBoard the whole time.

Two addressing forms appear here, both verified against production:

  flat    battery_voltage, ac_voltage, system_current, gateway_sts, cctv_sts,
          statusbox_* — present on 92-103 of the 128 devices.
  dotted  gateway.powerStatus, cctv.healthStatus, rock.HddINFO — a path INTO a
          nested JSON container, resolved by normalization.flatten.expand_containers.
          The fleet has ZERO literal dotted key names, so a dotted path must never be
          sent to ThingsBoard as-is; flatten.request_keys() reduces it to the
          container name first.
"""

# Battery status is a ladder over two flags (doc: Low & Reverse / Reverse / Low / OK),
# not one key.
_BATTERY_FLAGS = [
    "statusbox_battery_low",
    "statusbox_battery_reverse",
    "system_status.statusbox_battery_low",
    "system_status.statusbox_battery_reverse",
]

# Every subsystem exposes the same powerStatus/systemStatus/healthStatus triple plus a
# flat *_sts heartbeat.
_SUBSYSTEM = [
    "ias.powerStatus",
    "ias.systemStatus",
    "ias.healthStatus",
    "bas_sts",
    "bas.powerStatus",
    "bas.systemStatus",
    "bas.healthStatus",
    "fas_sts",
    "fas.powerStatus",
    "fas.systemStatus",
    "fas.healthStatus",
    "timeLock_sts",
    "timeLock.powerStatus",
    "timeLock.systemStatus",
    "timeLock.healthStatus",
    "timeLockDoor",
    "timeLockHealth",
    "accessControl_sts",
    "accessControl.powerStatus",
    "accessControl.systemStatus",
    "accessControl.healthStatus",
    "accessControlDoor",
    "accessControlHealth",
]

_CCTV_RECORDING = [
    "rock.VIDEOdETAILS",
    "rock.SdRecINFO",
    "rock.CAMERAdETAILS",
]

_CCTV_DEVICE = [
    "rock.Time",
    "rock.dexter_date",
    "rock.syncTimeDate",
    "rock.deviceType",
    "rock.manufacturer",
    "rock.model",
    "rock.serialNumber",
    "rock.firmwareVersion",
    "rock.mfDate",
]

INTENT_KEYS: dict[str, list[str]] = {
    # doc: "gateway status/SYSTEM STATUS", "Health Status"
    "gateway_status": [
        "gateway_sts",
        "gateway.systemStatus",
        "gateway.healthStatus",
        "gateway.powerStatus",
        "statusbox_system_on",
        "statusbox_system_healthy",
        "system_status.statusbox_system_on",
        "system_status.statusbox_system_healthy",
    ],
    # doc: "POWER STATUS", "MAINS STATUS/Power Supply Status"
    "power_status": [
        "gateway.powerStatus",
        "statusbox_mains_on",
        "system_status.statusbox_mains_on",
        "battery_voltage",
        "ac_voltage",
        "system_current",
        *_BATTERY_FLAGS,
    ],
    "battery_voltage": ["battery_voltage"],
    "battery_health": ["battery_voltage", *_BATTERY_FLAGS],
    "battery_low_status": _BATTERY_FLAGS,
    "ac_voltage": ["ac_voltage"],
    "system_current": ["system_current"],
    # doc: "Network /Network Status", "Operator /Network Operator"
    "network_status": [
        "statusbox_network",
        "system_status.statusbox_network",
        "statusbox_sos_status",
        "statusbox_no_of_connected_device",
        "system_status.statusbox_no_of_connected_device",
    ],
    # doc: "CCTV Status / CCTV Power Status", "Camera Status", "cctv heart beat"
    "cctv_status": [
        "cctv.powerStatus",
        "cctv.healthStatus",
        "cctv.systemStatus",
        "cctv_sts",
        "cameraTamperCount",
        "cameraDisconnectCount",
    ],
    # doc: "HDD Status"; the error COUNT is derived from rock.HddINFO[].HDDStatus
    "cctv_hdd_error_status": ["hddStatus", "rock.HddINFO"],
    # doc: "HDD Information" and "Cctv storage status" (sum of HDDCapacity)
    "cctv_hdd_info": ["rock.HddINFO"],
    "cctv_device_info": _CCTV_DEVICE,
    # doc: "NVR Recording Information", "SD Recording Information", "Camera Information"
    "cctv_recording_info": _CCTV_RECORDING,
    "cctv_recording_compliance": _CCTV_RECORDING,
    "subsystem_status": _SUBSYSTEM,
    "subsystem_fault_status": _SUBSYSTEM,
    "subsystem_alarm_status": _SUBSYSTEM,
    "device_hardware": [
        "rock.model",
        "rock.manufacturer",
        "rock.serialNumber",
        "rock.firmwareVersion",
        "rock.deviceType",
    ],
    "door_status": [
        "timeLockDoor",
        "accessControlDoor",
        "timeLock.systemStatus",
        "accessControl.systemStatus",
    ],
    # doc: "SOS Status", "Devices Connected"
    "sos_status": ["statusbox_sos_status"],
    "connected_devices": [
        "statusbox_no_of_connected_device",
        "system_status.statusbox_no_of_connected_device",
    ],
    # doc: "Cctv storage status" (sum of HDDCapacity) and "Total Camera"
    "cctv_storage": ["rock.HddINFO"],
    "cctv_camera_count": ["rock.VIDEOdETAILS", "rock.CAMERAdETAILS"],
    "cctv_camera_info": ["rock.CAMERAdETAILS"],
    "cctv_sd_recording": ["rock.SdRecINFO"],
    "cctv_tamper_count": ["cameraTamperCount", "cameraDisconnectCount"],
    # doc: basSystemIntegration.*
    "bas_panel_info": [
        "basSystemIntegration.basMainInfo",
        "basSystemIntegration.basAboutDevice",
    ],
    "bas_power_status": ["basSystemIntegration.basPowerStatus"],
    "bas_zone_info": ["basSystemIntegration.zoneInfo"],
    # Not covered by the key doc; kept so the intents still fetch something. Revisit
    # when the doc grows an access-control section rather than guessing more names.
    "fault_reason": ["Device_Issue"],
    "access_control_user_count": ["totalUsers", "registeredUsers"],
    "access_control_device_info": ["accessControl.powerStatus", "accessControl.systemStatus"],
}


def keys_for(intent: str) -> list[str]:
    """Keys to READ for an intent; [] when the intent has no profile.

    These may include dotted paths. Use normalization.flatten.request_keys() before
    passing them to ThingsBoard.
    """
    return list(INTENT_KEYS.get(intent, []))
