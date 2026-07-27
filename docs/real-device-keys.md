# Real Device Keys — Ground Truth

Confirmed ThingsBoard telemetry + server-attribute keys from the production fleet
(operator-provided). Use this to verify the normalization port and the not-yet-ported
answer layer. Deduped and grouped. Key names are case-sensitive; some contain spaces.

**Coverage legend:** ✅ v2 reads it · ⚠️ partial (v2 reads a narrower ladder) ·
❌ not ported (resolved only by the Java answer layer — `service/query/handler/*`).

## JSON containers (§10 — parse then look up nested keys)

`rock`, `rockAI`, `gatewayStatus`, `ticketStatus`, `cameraStatus`, `system_status`,
`battery_status`, `ac_status`, `current_status`, `alerts`, `attribute`, `dexter_config`

v2: resolver + mapper search `rock, rockAI, gatewayStatus, system_status, ticketStatus`
(and `dexter_config, cameraStatus` in the mapper). ⚠️ `battery_status`/`ac_status`/
`current_status`/`alerts`/`attribute` are containers here but v2 only reads their
*flattened* `<container>_<field>` forms — fine if the gateway flattens them.

## Gateway / connectivity

`gatewayStatus`, `status_device`, `gatewayUptime`, `gatewayLastHour`,
`uptimeTotal`, `uptimeHeartbeat`, `heartBeatCCTV/FAS/BAS/TL`, `TLheartbeatCount/TS`

- Gateway state ✅ (snapshot `gateway` — but real fleet leans on `status_device` /
  nested `gatewayStatus`; verify the flatten covers it).
- Uptime/heartbeat/lastHour ❌ → Java `UptimeQueryHandler` / `HistoricalQueryHandler`.

## Power / battery

`battery_status`, `bat_result`, `BATTERY LOW`, `statusbox_battery_low`,
`ac_status`, `ac_result`, `current_status`, `cur_result`, `power_off_last`,
`MAINS ON` (implied), `BATTERY REVERSE` (implied)

- battery voltage ✅ (`battery_status_battery_voltage`); AC ✅; current ✅; mains ✅.
- ⚠️ battery **status** (Low / Reverse / Low&Reverse): v2 reads only `BATTERY LOW`.
  Java `AnswerSupport.resolveBatteryStatus` also reads `statusbox_battery_low`,
  `system_status_statusbox_battery_low`, `gatewayStatus_BATTERY LOW`, and the
  `*_battery_reverse` variants. ❌ reverse + statusbox ladder not ported.
- `bat_result`/`ac_result`/`cur_result` summary keys ⚠️ (in `key_profiles`, not read by snapshot).

## CCTV

`cameraStatus`, `hikvision_camera_status`, `dahua_camera_status`,
`Hikvision_NVR_CameraRecInfo`, `Dahua_NVR_CameraRecInfo`, `CP_Plus_NVR_CameraRecInfo`,
`Hik_SD_card_rec_info_list`, `Dahua_SD_card_rec_info_list`, `Hik_SD_card_info`,
`Dahua_SD_card_info`, `cameraDisconnect`, `cameraDisconnect_CHID1..16_duration`,
`cameraDisconnect_fault`, `cameraTamper`, `hddError`, `hdd_error_count`,
`hdd_error_last`, `camera_disconnect_count`, `camera_disconnect_last`,
`camera_tampered_count`, `camera_tampered_last`, `lowDurationCameras`,
`all_ch_disconnect_scoreSum`, `channelID`, `cavlidata_ontime`, `cctvUptime`,
`cctvLastHour`, `Hikvision_NVR_Heartbeat`, `DahuaNVR_Heartbeat`, NVR date/time

- CCTV state + camera count ⚠️: snapshot counts from `*_CAMERAdETAILS` / NVR RecInfo.
- NVR device info (vendor/model/HDD slots/capacity/resolution), per-channel recording
  days + single-branch compliance, and vendor HDD-slot schemas (Hik + Dahua) ✅ ported to
  `app/query/cctv.py` (intents `cctv_device_info`, `cctv_recording_info`, upgraded
  `cctv_hdd_info`/`cctv_hdd_error_status`).
- Still ❌: fleet-wide recording compliance (needs multi-branch snapshots),
  per-channel disconnect history (`cameraDisconnectCH<n>_history`), `lowDurationCameras`
  cross-check, and the `Hik/Dahua_SD_card_rec_info_list` count ladder.

## Subsystems — IAS / BAS / FAS

`iasBasFasStatus`, `intrusion_alarm_system_fault`, `INTRUSION ALARM SYSTEM FAULT`,
`intrusion_alarm_system_activate/off`, `intrusion_alarm_activate_last`,
`fire_alarm_system_fault`, `fire_alarm_system_off`, `fire_alarm_system_activate`,
`fire_alarm_activate_last/fault_last/off_last`, `fireAlarmSystemFault`,
`intrusionAlarmSystemFault`, `BASfaultCOUNT`, `BASinactiveCOUNT`,
`FASfaultCOUNT`, `FASinactiveCOUNT`, `basUptime`, `fasUptime`,
short codes: `iasf`, `fasf`, `basLastHour/fasLastHour`

- Subsystem `_sts` roll-up ✅ (snapshot `subsystems`) — but only if `ias_sts` etc. exist.
- ❌ **Fault / alarm resolution** (`iasBasFasStatus_*`, `ticketStatus_*_FAULT/ACTIVATE`,
  `*_COUNT`) is the biggest gap → Java `AnswerSupport.resolveSubsystemFault/Alarm` +
  `SubsystemHandler`. v2's `subsystem_status` returns only the `_sts` state.

## Time Lock / Access Control

`tlsAcsStatus`, `time_lock_system_off`, `time_lock_door_open_count`,
`time_lock_tamper_count`, `time_lock_off_last`, `TLofftimeTS`, `heartBeatTL`,
`access_control_door_open_count`, `access_control_system_tamper_count`,
`accessControlLastHour`, short codes: `tld`, `tlt`, `acd`, `acst`

- ❌ door status, tamper/door-open counts → Java `DoorStatusHandler`,
  `AccessControlHandler`, `AnswerSupport.resolveSubsystemAlarm` (count-based).

## Hardware / system

`cpu`, `memory`, `disk`, `temperature`, `frequency`, `net_recv_mb`, `net_sent_mb`,
`rpi_usage`, `rpi_alert`, `usage_daily`, `usage_last_7_days`, `usage_last_15_days`,
`Total_Data_Usage`, `watchdog_log`, `sw_state`, `target_sw_version/tag/title/ts`,
`imei_id`, `ctemp`

- CPU/mem/disk/temp + sw version ✅ (snapshot `hardware`, `key_profiles device_hardware`).
- ❌ network throughput, data usage, watchdog, IMEI → Java `DeviceHardwareHandler`,
  `NetworkStatusHandler`, `DeviceIdentityHandler`.

## Location

`lat`, `lon`, `lat1`, `lon1`, `arrLat`, `arrLon`, `TotalLat`, `TotalLon` — ❌ GPS (no handler in v2).

## Alerts / counts / misc

`alarm`, `alarmCount`, `error`, `errorCount`, `alerts`, `ticketStatus`, `Device_Issue`,
`mail`, `branch_id`, `id`, `date`, `time`, `timestamp`, `log_type`, `calculatedMonth`

- `alarmCount`/`errorCount` + boolean alert flags ✅ (snapshot `alerts`).
- `Device_Issue` (fault reason) ❌ → Java `FaultReasonHandler`.
- The `*_count` family (fault/tamper/disconnect counts) ❌ → `AnswerSupport.resolveFromCount`.

## Bottom line

v2 covers the **snapshot core** (gateway, battery voltage, AC, current, CCTV count,
hardware, subsystem `_sts` state, basic alerts). The un-ported **answer layer**
(`service/query/handler/*`, 28 handlers + `AnswerSupport`) is where most listed keys
resolve: subsystem fault/alarm ladders, count-based flags, rich battery status,
per-channel CCTV, door/access-control, network, GPS, uptime/historical, fault reason.
That layer is the next slice.
