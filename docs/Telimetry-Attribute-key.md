# Integration Mapping Document

| Key | Intent | Description / Logic |
| --- | --- | --- |
| gateway_sts | gateway status/SYSTEM STATUS | |
| gateway.powerStatus | POWER STATUS | |
| gateway.systemStatus | Health Status | |
| system_status.statusbox_mains_on | MAINS STATUS/Power Supply Status | |
| statusbox_network | Network /Network Status | if statusbox_network exists<br>    if not empty<br>        Status = On<br>        Operator = Network Name<br>    else<br>        Status = Off<br>        Operator = "-" |
| statusbox_network /system_status.statusbox_network | Operator /Network Operator | |
| statusbox_sos_status | SOS Status | |
| System_status.statusbox_no_of_connected_device / statusbox_no_of_connected_device | Devices Connected /Connected Devices | |
| Statusbox_battery_reverse && statusbox_battery_low | Battery Status | if Battery Low and Battery Reverse<br>    Status = Low & Reverse<br>else if Battery Reverse<br>    Status = Reverse<br>else if Battery Low<br>    Status = Low<br>else<br>    Status = OK |
| battery_voltage | Battery Voltage Status | |
| system_current | Current Status/System Current Status | |
| ac_voltage | AC Voltage Status | |
| | | |
| cctv.powerStatus | CCTV Status / CCTV Power Status | |
| cctv.healthStatus | Camera Status | |
| systemStatus | Camera Link Status | if valid systemStatus<br>    if Normal / Online / Active<br>        Status = Online<br>    else<br>        Status = Offline<br>else<br>    Status = N/A |
| hddStatus | HDD Status | |
| cctv_sts | cctv status/cctv heart beat | |
| cctv_sts | Cctv system status | |
| const hddInfo = data["rock.HddINFO"];<br><br>let totalCapacity = 0;<br><br>for (const hdd of hddInfo) {<br>    totalCapacity += parseFloat(hdd.HDDCapacity);<br>}<br><br>console.log(totalCapacity); | Cctv storage status | |
| const count = rock.VIDEOdETAILS.length;<br><br>console.log(count); | Total Camera | |
| | | |
| rock.HddINFO.forEach(hdd => {<br>    console.log(<br>        hdd.HDDSlot,<br>        hdd.HDDStatus,<br>        hdd.HDDCapacity,<br>        hdd.HDDFreeSpace<br>    );<br>}); | HDD Information | |
| rock.Time<br>Rock.dexter_date<br>rock.syncTimeDate<br>rock.deviceType<br>Rock.manufacturer<br>Rock.model<br>rock.serialNumber<br>rock.firmwareVersion<br>rock.mfDate | Cctv about device | |
| rock.Time | System Time | |
| rock.dexter_date | System Date | |
| rock.syncTimeDate | Last Sync. Date & Time | |
| rock.deviceType | Device Type | |
| rock.manufacturer | Device Make /manufacturar | |
| rock.model | Device Model | |
| rock.serialNumber | Device Serial Number /<br>Serial Number | |
| rock.firmwareVersion | Firmware Version | |
| rock.mfDate | MF Date /Manufacturar date | |
| rock.CAMERAdETAILS.forEach(camera => {<br>    console.log(<br>        `${camera.channel_no} | ${camera["Channel Name"]} | ${camera.manufacturer} | ${camera.model} | ${camera.serialNumber} | ${camera.resolution} | ${camera.fps} | ${camera["IP Address"]} | ${camera.status} | ${camera.TotalBytes}`<br>    );<br>}); | Camera Information | |
| rock.VIDEOdETAILS.forEach((camera, index) => {<br>    console.log(<br>        `${index + 1} | ${camera.cameraName} | ${camera.cameraIP} | ${camera.start_time} | ${camera.end_time} | ${camera.total_duration} days`<br>    );<br>}); | NVR Recording Information/<br>Recording information | |
| rock.SdRecINFO.forEach((camera) => {<br>    console.log(<br>        `${camera.channel_no} | ${camera.channel_name} | ${camera.cameraIP} | ${camera.start_time} | ${camera.end_time} | ${camera.total_recording_days} days`<br>    );<br>}); | SD Recording Information | |
| cameraTamperCount | Cctv camera tamper count | |
| cameraDisconnectCount | Cctv camera disconnect count | |
| let errorCount = 0;<br><br>rock.HddINFO.forEach(hdd => {<br>    if (hdd.HDDStatus.toLowerCase() === "error") {<br>        errorCount++;<br>    }<br>});<br><br>console.log(errorCount); | Cctv hdd error count | |
| ias.powerStatus | Ias power status/integrated alarm system power status | |
| ias.systemStatus | Ias system status | |
| ias.healthStatus | Ias health status | |
| bas.powerStatus | Bas power status/Barglar Alarm System power Status | |
| bas_sts | Bas system status | |
| bas.healthStatus | Bas health status | |
| basSystemIntegration.heartbeat | Bas heartbeat | |
| basSystemIntegration.panelState | Bas panel state | |
| basSystemIntegration.panelMode | Bas panel mode | |
| basSystemIntegration.zoneInfo.forEach((zone, index) => {<br>    console.log(<br>        `${index + 1} | ${zone.zoneName} | ${zone.areaStates} | ${zone.bell} | ${zone.zoneEvent} | ${zone.timeStamp}`<br>    );<br>}); | Bas zone information | |
| basSystemIntegration.basAboutDevice.panelIp | Bas about device panel ip | |
| basSystemIntegration.basAboutDevice.model | Bas about device model | |
| basSystemIntegration.basAboutDevice.firmwareVersion | Bas Firmware Version | |
| basSystemIntegration.basAboutDevice.lastUpdated | Bas Last Updated | |
| basSystemIntegration.basPowerStatus.systemVoltage | Bas Power Status<br>System voltage | |
| basSystemIntegration.basPowerStatus.batteryVoltage | Bas batteryVoltage | |
| basSystemIntegration.basPowerStatus.systemCurrent | Bas systemCurrent | |
| basSystemIntegration.basPowerStatus.batteryCurrent | Bas batteryCurrent | |
| basSystemIntegration.basPowerStatus.batteryStatus | Bas batteryStatus | |
| basSystemIntegration.basPowerStatus.mainStatus | Bas mainStatus | |
| fas.powerStatus | Fas power status | |
| fas_sts | Fas system status | |
| fas.healthStatus | Fas health status | |
| timeLock.powerStatus | Tls power status/Time Lock power status | |
| timeLock_sts | Tls system status | |
| timeLockDoor | Tls DOOR STATUS | |
| timeLockHealth | Tls health status | |
| accessControl.powerStatus | Acs power status/access control power status | |
| accessControl_sts | Acs system status | |
| accessControlDoor | Acs door status | |
| accessControlHealth | Acs health status | |
| accessControl.systemStatus | Acs system status | |
| | | |
