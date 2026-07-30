# ThingsBoard Key Map — Source of Truth for Normalization

Extracted from the production Java bot (`IntentKeyProfileRegistry`, `BranchSnapshotMapper`,
`FieldPrecedenceResolver`, `ValueNormalizer`). This document is the CONTRACT for the v2
`normalization/` module: key names, precedence order, and fallback ladders must be ported
exactly. Key names are case-sensitive and some contain spaces (e.g. `BATTERY LOW`,
`gatewayStatus_MAINS ON`) — they are literal ThingsBoard telemetry/attribute keys.

## 1. ThingsBoard API endpoints used

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/login` | service-account JWT |
| `GET /api/auth/user` | resolve current user/customer from a token |
| `GET /api/customer/{id}/devices`, `GET /api/customer/devices` | device list per customer (TB ACL applies) |
| `GET /api/tenant/devices` | full tenant device inventory |
| `GET /api/customers`, `GET /api/user/users` | customer + user sync |
| `GET /api/plugins/telemetry/DEVICE/{id}/values/timeseries?keys=...` | telemetry, intent-specific keys |
| `GET /api/plugins/telemetry/DEVICE/{id}/values/attributes/{CLIENT_SCOPE\|SERVER_SCOPE}` | attributes |
| `GET /api/alarms/DEVICE/{id}` | alarms |
| `GET /api/audit/logs` | audit log queries |
| `POST /api/entitiesQuery/find`, `POST /api/queries/entityData` | bulk latest-value queries (fleet sync) |

## 2. Intent -> telemetry keys (query key profiles)

| Intent | Keys (request exactly these) |
|---|---|
| GATEWAY_STATUS | `gateway`, `status`, `gatewayStatus` |
| BATTERY_VOLTAGE / BATTERY_HEALTH | `battery_status_battery_voltage`, `gatewayStatus_battery_voltage`, `BATTERY LOW` |
| BATTERY_LOW_STATUS | `BATTERY LOW`, `gatewayStatus_BATTERY LOW`, `ticketStatus_BATTERY_LOW` |
| POWER_STATUS | `battery_status_battery_voltage`, `gatewayStatus_battery_voltage`, `BATTERY LOW`, `gatewayStatus_ac_voltage`, `ac_status_ac_voltage`, `ac_result`, `MAINS ON`, `gatewayStatus_MAINS ON`, `system_status_statusbox_mains_on` |
| AC_VOLTAGE | `gatewayStatus_ac_voltage`, `ac_status_ac_voltage`, `ac_result` |
| SYSTEM_CURRENT | `gatewayStatus_system_current`, `current_status_system_current`, `cur_result` |
| NETWORK_STATUS | `gatewayStatus_NETWORK`, `system_status_statusbox_network`, `networkOperator` |
| CCTV_STATUS | `cctv_sts`, `cameraStatus_cctvStatus`, `cctvStatus`, `cctv_status`, `cameraLinkStatus`, `rock_CAMERAdETAILS`, `CAMERAdETAILS`, `Hikvision_NVR_CameraRecInfo`, `rock_VIDEOdETAILS`, `hikvision_camera_status` |
| CCTV_HDD_ERROR_STATUS | `HDD ERROR`, `ticketStatus_HDD_ERROR`, `cameraStatus_HDD ERROR`, `hddStatus` |
| CCTV_HDD_INFO | `rock_HddINFO`, `hddStatus` |
| CCTV_RECORDING_INFO / COMPLIANCE | `rock_VIDEOdETAILS`, `VIDEOdETAILS`, `Hikvision_NVR_CameraRecInfo`, `Dahua_NVR_CameraRecInfo`, `CP_Plus_NVR_CameraRecInfo`, `Hik_rock_NVR1_VIDEOdETAILS`, `Hik_rock_NVR2_VIDEOdETAILS` |
| SUBSYSTEM_STATUS / FAULT / ALARM | `iasStatus`, `basStatus`, `fasStatus`, `timeLock`, `timeLockHealth`, `accessControlStatus`, `ticketStatus_IAS_FAULT`, `ticketStatus_FAS_FAULT`, `BASfaultCOUNT`, `ticketStatus_TLS_TAMPER`, `ticketStatus_ACS_TAMPER`, `ticketStatus_IAS_ACTIVATE`, `ticketStatus_FAS_ACTIVATE`, `ticketStatus_TLS_DOOR_OPEN`, `ticketStatus_ACS_DOOR_OPEN` |
| DEVICE_HARDWARE | `cpu`, `memory`, `disk`, `temperature`, `target_sw_version`, `sw_state`, `sw_version` |
| FAULT_REASON | `Device_Issue` |
| DOOR_STATUS | `timeLockDoor`, `accessControlDoor` |
| ACCESS_CONTROL_USER_COUNT | `accessControlTotalUsers`, `totalUsers`, `registeredUsers`, `accessControlUserCount` |
| ACCESS_CONTROL_DEVICE_INFO | `accessControlModel`, `biometricModel`, `acsModel`, `accessControlFirmware`, `biometricFirmware`, `accessControlFirmwareVersion`, `accessControlIp`, `biometricIp`, `accessControlIP` |

## 3. Subsystem state — `*_sts` server attributes are AUTHORITATIVE

A device without its `_sts` key reports UNKNOWN — never guess subsystem state from
operational flags. Present `_sts` values (Online/Offline/Fault/N/A) are shown as-is.

| Subsystem | Primary key | Fallback status keys | Health/detail key(s) |
|---|---|---|---|
| CCTV | `cctv_sts` | `cameraStatus_cctvStatus`, `cctvStatus`, `cctv_status`, `cameraLinkStatus`, `cctv_state`, `rock_cctv_status` | `cctvStatus`, `cameraLinkStatus` |
| IAS (intrusion) | `ias_sts` | — | `iasStatus`, `ias_status` |
| BAS | `bas_sts` | — | `basStatus` |
| FAS (fire) | `fas_sts` | — | `fasStatus`, `fireAlarmStatus` |
| Time Lock | `timeLock_sts` | — | `timeLockHealth` |
| Access Control | `accessControl_sts` | — | `accessControlStatus` |

Per-subsystem flattened detail attributes (prefix = `_sts` key minus suffix):
`<prefix>_powerStatus`, `<prefix>_systemStatus`, `<prefix>_logStatus`, `<prefix>_healthStatus`
(e.g. `cctv_powerStatus`). Shown verbatim, including `N/A`.

CCTV dynamic fallback (only when `cctv_sts` is UNKNOWN/absent):

- online camera count > 0 -> ONLINE
- else gateway OFFLINE -> OFFLINE
- else stay UNKNOWN

`N/A`/`null` primary value -> NOT_INSTALLED.

## 4. Gateway state — precedence chain (first parseable hit wins)

1. TB connectivity attribute (authoritative): `active`, `serverAttributes_active`,
   `device_active`, `gateway_active` — `false/0/offline` -> OFFLINE, `true/1/online` -> ONLINE.
2. Fallback chain, in order:
   `status_device_gateway_status`, `statusbox_system_healthy`,
   `system_status_statusbox_system_healthy`, `rock_healthyStatus`, `healthyStatus`,
   `gwHealth`, `gateway_sts`, `gateway_status`, `gatewayStatus_status`,
   `rock_gateway_status`, `status`, `statusbox_system_on`,
   `system_status_statusbox_system_on`, `gatewayStatus_SYSTEM ON`

Extra: `gwHealth` -> gateway health string; `active` -> boolean.
Conflict between `gateway` and `status` values -> record a warning, do not fail.

## 5. Power — precedence rules

| Metric | Rule |
|---|---|
| Battery voltage | `battery_status_battery_voltage` wins; `gatewayStatus_battery_voltage` only as fallback; both present and different -> warning |
| AC voltage | `ac_status_ac_voltage` only |
| System current | `current_status_system_current` only |
| Mains on | first parseable of: `statusbox_mains_on`, `system_status_statusbox_mains_on`, `MAINS ON`, `gatewayStatus_MAINS ON` |
| Battery low | `BATTERY LOW` boolean flag |

## 6. CCTV camera counting — 5-step fallback ladder (stop at first success)

1. `rock_CAMERAdETAILS` / `CAMERAdETAILS` / `CAMERA_DETAILS` / `CAMERADETAILS` — JSON array;
   per-camera status key: `cameraStatus` | `status` | `Active Status` | `active_status` |
   `camera_status`; online when status is null/active/online/on/1/true.
2. Recording-info arrays: `rock_VIDEOdETAILS`, `VIDEOdETAILS`, `rock_SdRecINFO`, `SdRecINFO`,
   `Hikvision_NVR_CameraRecInfo`, `Dahua_NVR_CameraRecInfo`, `CP_Plus_NVR_CameraRecInfo`,
   `CameraRecInfo` — camera online when `total_duration` > 0 (fallback
   `total_recording_days`) or `start_time` set and not `N/A`. Use when count exceeds step 1.
3. `dexter_config` JSON object -> `integration[].camera_ip[]` -> count = array size (all online).
4. `CAMERA DISCONNECT CH <n>` keys — total = max channel number; disconnected = keys with
   value true/1/yes; online = total - disconnected.
5. Direct count attributes: `count_camera`, `no_of_connected_cctv`, `cctv_count`,
   `no_of_cameras`, `total_cameras`, `Hikvision_NVR_NoOfCameras`.

HDD status: `rock_HddINFO` / `HddINFO` / `HDD_INFO` JSON array -> first element `HDDStatus`.

## 7. Alert/alarm flags (booleans + counters)

`alarmCount` (int), `errorCount` (int), `DVR/NVR OFF`, `ticketStatus_NVR_OFF`, `HDD ERROR`,
`CAMERA DISCONNECT`, `CAMERA TAMPER`, `INTRUSION ALARM SYSTEM ACTIVATE`,
`FIRE ALARM SYSTEM ACTIVATE`, `POWER OFF`, `BATTERY LOW`

## 8. Hardware health keys

`cpu`, `memory`, `disk`, `temperature` (doubles); software: `target_sw_version`, `sw_state`,
`sw_version`.

## 9. Identity + hierarchy keys

- Branch display name precedence: `branchName` -> `formattedBranchName` -> `device_name`
  -> `deviceName`; uppercased + trimmed.
- Technical id precedence: `device_name` -> `deviceName` -> `formattedBranchName` -> `branchName`.
- Hierarchy path: `full_path` — telemetry first, server attributes fallback; segments
  separated by `→` (unicode arrow; `->` accepted). Also `branch_name` server attribute.
- Ids: `device_id`, `branch_id`.
- Aliases generated per branch: raw, minus bank prefix (`BOI-`), dashes->spaces,
  minus `BRANCH`, whitespace-stripped variants.

## 10. Nested JSON containers

When a key is missing at top level, search inside these attributes whose values are
JSON-encoded strings: `rock`, `gatewayStatus`, `system_status`, `ticketStatus`, `rockAI`,
`dexter_config`, `cameraStatus`. Parse the string, look up the target key inside.

## 11. Value normalization table (apply to every raw value)

| Normalized state | Raw values (case-insensitive) |
|---|---|
| ONLINE | online, on, healthy, active, true, 1, yes, clear, normal |
| OFFLINE | offline, off, inactive, false, 0, disconnected |
| FAULT | fault, alarm, error, tamper, triggered, critical |
| NOT_INSTALLED | n/a, na, not installed, `-` |
| UNKNOWN | everything else |

Corrupt/null handling: null, blank, `null`, values starting with `null`, `not_found`,
`not found` -> treat as absent (UNKNOWN / no metric). Numeric parsing: `N/A`, `na`, `-`
-> null; unparseable -> null (metrics) or fallback (ints).

Boolean mapping: true/1/yes/on/healthy/online -> true; false/0/no/off/offline/fault/inactive
-> false; else null.
