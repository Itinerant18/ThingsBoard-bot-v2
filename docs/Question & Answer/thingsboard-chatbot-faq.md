# ThingsBoard Chatbot FAQ

This document contains expected questions from users and the corresponding answers the chatbot should provide based on ThingsBoard system data. These Q&A pairs will guide the system prompt design and serve as few-shot examples for the agent.

## System Prompt Guidelines
To achieve this level of answering, the system prompt must:
1. **Instruct the agent to provide exact figures and percentages** (e.g., "48 of 52 devices are healthy (92.3%)").
2. **Prioritize unresolved/open issues** when asked for general status (e.g., highlighting the 1 faulty CCTV device, 2 offline Gateways).
3. **Understand device categories** (CCTV, Gateway, TLS, ACS, IAS, BAS, FAS) and be able to aggregate their health.
4. **Calculate and state Turnaround Times (TAT)** for resolved alarms and identify unresolved alarms by their missing "end time".
5. **Pinpoint specific locations/branches** (e.g., BALLYBAZAR, DOBSON, LILUAH) and their associated active issues.

---

## Question & Answer Reference

**Q: What is the current status of our BOI system?**
A: The system is largely healthy: 48 of 52 devices are healthy (92.3%). There is 1 faulty device, 3 offline devices, and 1 open alert requiring attention.

**Q: Which device category has the most faulty devices?**
A: CCTV has the most faulty devices, with 1 faulty CCTV device. All other deployed categories currently show zero faulty devices.

**Q: Which device category has the most offline devices?**
A: Gateway has the most offline devices with 2, followed by TLS with 1.

**Q: Is the CCTV system healthy?**
A: CCTV is mostly healthy: 10 of 11 CCTV devices are healthy, 1 is faulty, and none are offline.

**Q: Is the Gateway system healthy?**
A: Gateway is mostly healthy: 19 of 21 devices are healthy, with 2 currently offline and none faulty.

**Q: How many TLS devices are offline?**
A: One TLS device is currently offline; 2 of 3 TLS devices are healthy.

**Q: Are any ACS devices deployed?**
A: No. The dashboard currently shows 0 Access Control System devices deployed.

**Q: What needs attention now?**
A: Priority items are the 1 faulty CCTV device, 2 offline Gateway devices, 1 offline TLS device, and the 1 open alert.

**Q: Are there any active unresolved alarms?**
A: Yes. The alarm history lists unresolved entries with “No Data” for end time, including a BALLYBAZAR camera disconnect on channel 15 and camera-tamper alerts.

**Q: What is the current camera issue at BALLYBAZAR?**
A: The dashboard shows a BOI Camera Disconnect CH 15 alarm at BALLYBAZAR, created on July 24, 2026 at 15:52, with no end time shown.

**Q: What is the current health status of all IAS devices?**
A: All 4 Integrated Alarm System (IAS) devices are healthy. There are no faulty or offline IAS devices.

**Q: What is the current health status of all BAS devices?**
A: All 7 Burglar Alarm System (BAS) devices are healthy, with no faulty or offline devices.

**Q: What is the current health status of all FAS devices?**
A: All 6 Fire Alarm System (FAS) devices are healthy. No FAS devices are faulty or offline.

**Q: What is the health distribution across all device categories?**
A: Gateway: 19 healthy, 0 faulty, 2 offline; CCTV: 10 healthy, 1 faulty, 0 offline; IAS: 4 healthy; BAS: 7 healthy; FAS: 6 healthy; TLS: 2 healthy, 1 offline; ACS: no deployed devices.

**Q: What is the real-time health status of all monitored systems?**
A: Of 52 monitored devices, 48 are healthy (92.3%), 1 is faulty (1.9%), and 3 are offline (5.8%).

**Q: Which device category currently has the lowest health percentage?**
A: TLS has the lowest health percentage: 2 of 3 devices are healthy, or 66.7%.

**Q: Is there any camera disconnect alarm currently active?**
A: Yes. A major BOI Camera Disconnect CH 15 alarm at BALLYBAZAR, created on July 24 at 15:52, has no recorded end time.

**Q: Is there any camera tamper alarm currently active?**
A: Yes. The dashboard lists unresolved camera-tamper alarms, including one at DOBSON created on July 17 at 15:11 and one at BALLYBAZAR created on July 15 at 18:13.

**Q: Is there any Integrated Alarm System activation currently active?**
A: The latest IAS activation shown was at LILUAH on July 26. It was resolved after approximately 3 minutes, so it is not currently active.

**Q: What was the latest resolved alarm?**
A: The most recent resolved alarm was a major BOI Integrated Alarm System Activate event at LILUAH, created July 26 at 07:24 and resolved at 07:26, with a TAT of about 3 minutes.

**Q: What is the longest resolved alarm TAT shown?**
A: The longest resolved alarm displayed is a Fire Alarm System activation at SEPL-DX2, with a TAT of approximately 6 hours on July 16.

**Q: Are there any fire alarm activations right now?**
A: No current fire-alarm activation is shown in the available dashboard data. The Fire Alarm System activation at SEPL-DX2 on July 16 was resolved.

**Q: Are there any burglar alarm activations right now?**
A: No active burglar alarm is visible in the available alarm table. The dashboard does show resolved Intrusion Alarm System events at SEPL-DX2 on July 23.

**Q: Which branches have unresolved alarms in the displayed data?**
A: The displayed unresolved alarms are at BALLYBAZAR and DOBSON, as their entries have no end time recorded.

**Q: What alarm types are visible in the dashboard?**
A: The visible alarm types include Camera Disconnect, Camera Tamper, Integrated Alarm System Activate, Intrusion Alarm System Activate, and Fire Alarm System Activate.

**Q: Which region is currently active?**
A: One region is currently active: FGMO.

**Q: Which branch is visible on the map?**
A: The dashboard map currently shows BOI-TARAKESHWAR as an online marker.

**Q: What is the oldest currently active alarm?**
A: The oldest unresolved alarm visible is a Camera Tamper warning at BALLYBAZAR, created on July 15, 2026 at 18:13. Its end time is still not recorded.

**Q: Which active alarm has been open the longest?**
A: The longest-open unresolved alarm displayed is the BALLYBAZAR Camera Tamper alarm, open since July 15 at 18:13.

**Q: What is the severity breakdown of the visible active alarms?**
A: The displayed unresolved alarms include one Major alarm—BALLYBAZAR Camera Disconnect CH 15—and two Warning alarms—Camera Tamper alarms at DOBSON and BALLYBAZAR.

**Q: What alarms were triggered in the last 24 hours?**
A: No alarms from the previous 24 hours are visible in the dashboard table. The latest listed event is an IAS activation at LILUAH on July 26, 2026.

**Q: What alarms were triggered in the last hour?**
A: No alarms triggered within the last hour are shown in the currently available alarm table.

**Q: Are there alarms at BALLYBAZAR?**
A: Yes. BALLYBAZAR has an unresolved major Camera Disconnect CH 15 alarm and an unresolved Camera Tamper warning. It also has a previously resolved Camera Disconnect event from July 15.

**Q: Are there alarms at DOBSON?**
A: Yes. DOBSON has an unresolved Camera Tamper warning created on July 17 at 15:11. A Camera Disconnect alarm at DOBSON was resolved about three minutes later.

**Q: Are there alarms at LILUAH?**
A: LILUAH had a major Integrated Alarm System activation on July 26. It was resolved in approximately three minutes, so it is not currently open.

**Q: Which branches currently need immediate attention?**
A: BALLYBAZAR needs highest priority due to an unresolved major camera-disconnect alarm and an unresolved camera-tamper warning. DOBSON also requires attention because of an unresolved camera-tamper warning.

**Q: What is the current TAT for active alarms?**
A: The open alarms do not have an end time, so a final TAT is not available in the table. Their live duration should be calculated from the creation time until the current time.

**Q: What is the longest completed TAT in the displayed alarm history?**
A: The longest completed TAT shown is 6 hours for a Fire Alarm System activation at SEPL-DX2, created July 16 and resolved the same day.

**Q: Which zone has visible unresolved alarms?**
A: All displayed unresolved alarms are in the HOWRAH zone under the EAST FGMO.

**Q: Are there any active critical alarms?**
A: No alarms labeled “Critical” are visible. The highest severity among the unresolved alarms displayed is Major.
