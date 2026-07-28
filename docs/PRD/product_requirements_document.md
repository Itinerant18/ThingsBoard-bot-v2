# Product Requirements Document (PRD)
## Smart Assistant for IoT (SAI) — Intelligent Multi-Branch Fleet Operations Assistant

---

## 1. Executive Summary & Product Vision

### 1.1 Executive Summary
The **Smart Assistant for IoT (SAI)** is an enterprise-grade conversational AI platform designed to simplify multi-branch facility and security management across highly distributed physical networks (e.g., bank branches, retail networks, commercial real estate). 

Instead of forcing security teams, facility managers, and field technicians to navigate complex dashboard structures, click through multi-tiered menus, or decipher raw technical telemetry logs, SAI provides a natural language conversational interface. Users can ask questions in plain English (e.g., *"Are there any inactive branches in the West Region?"*, *"What is the CCTV status of BALLY BAZAR?"*, *"Show me battery voltage for Main Branch"*) and receive instant, structured, and verified status reports accompanied by interactive follow-up recommendations.

### 1.2 Product Vision & Core Mission
**"Your IoT Data, Simplified: Ask. Analyze. Act. Done."**

- **Zero Dashboard Friction:** Eliminate the learning curve and time required to assess physical facility health.
- **Absolute Ground-Truth Accuracy:** Implement a Context-Augmented Generation model where live device telemetry is deterministically queried and verified from the live state repository *before* outputting answers, guaranteeing zero hallucinated device states.
- **Strict Role-Based Multi-Tenancy:** Ensure strict organizational scope boundaries (Head Office, Region, Zone, Branch) where users only see devices and branches they are authorized to view.
- **Actionable Intelligence:** Transform raw numeric inputs (like camera disconnections or voltage drops) into clear operational insights, complete with channel grouping, fault diagnostics, and targeted follow-up suggestions.

---

## 2. The Problems We Are Resolving (In-Depth)

The core value of SAI is rooted in solving specific operational bottlenecks inherent in large-scale IoT deployments. We are explicitly resolving the following critical issues:

### 2.1 Information Overload & Alert Fatigue
**The Problem:** A single modern bank branch may have 143+ connected devices (cameras, doors, UPS units, gateways). Across 1,000 branches, this results in hundreds of thousands of data points. Security Operations Center (SOC) analysts suffer from alert fatigue and struggle to isolate critical faults from routine telemetry noise.
**How We Resolve It:** SAI acts as an intelligent filter. Instead of showing all 143 devices, when a user asks about CCTV status, SAI aggregates the data, groups identical failures (e.g., "Channels 5-8 are offline"), and presents only the actionable delta. 

### 2.2 The Dashboard Accessibility Barrier
**The Problem:** Traditional IoT dashboards require extensive training. Non-technical staff (e.g., a regional bank manager or a local security guard) often cannot navigate complex hierarchies, drill-down menus, or interpret raw sensor graphs.
**How We Resolve It:** SAI completely flattens the UI into a chat window. If a user can send a text message, they can query the entire facility network. The learning curve is reduced to zero.

### 2.3 Latency in Root-Cause Identification
**The Problem:** When a camera goes offline, the dashboard shows a red icon. However, understanding *why* it went offline (Did the branch lose power? Did the network gateway crash? Is the hard drive failing?) requires cross-referencing multiple different dashboard screens.
**How We Resolve It:** SAI resolves multi-domain telemetry instantly. Through handlers like the `FaultReasonHandler`, the bot evaluates interconnected subsystems and provides plain-English root-cause analysis (e.g., *"Branch X is showing a fault because Mains Power failed and the UPS battery is critically low"*).

### 2.4 Context Switching & Inefficient Workflows
**The Problem:** Field technicians waste time pulling up a branch, checking power, navigating back to a menu, selecting security, and checking doors.
**How We Resolve It:** Conversational memory allows continuous, fluid investigation. A user asks *"What is the power status of Branch X?"* and can simply follow up with *"What about the doors?"* The system seamlessly carries the branch context forward, mimicking a natural human conversation.

---

## 3. Strict Limitations & What We CANNOT Resolve (Out of Scope)

To maintain system security, integrity, and scope, it is equally important to define what SAI **cannot** do. These are hard constraints designed into the system architecture:

### 3.1 No Device Remediation or Write Actions
**Limitation:** SAI is strictly a **Read-Only / Diagnostic** tool. 
**What We Cannot Resolve:** 
- The bot **cannot** reboot a crashed router.
- The bot **cannot** remotely unlock a time-locked vault door.
- The bot **cannot** acknowledge or clear security alarms.
- The bot **cannot** change sensor thresholds or update device firmware. 
*Rationale:* Providing an AI agent with write-access to physical security infrastructure introduces unacceptable operational and security risks. All remediation must occur through official, authenticated control platforms or physical intervention.

### 3.2 No Physical World Verification
**Limitation:** SAI relies entirely on digital telemetry reported by the hardware.
**What We Cannot Resolve:**
- The bot **cannot** tell you if a camera lens has been sprayed with paint or physically covered (unless the camera hardware specifically supports and triggers a tamper alarm).
- The bot **cannot** verify if a locked door has been physically breached by brute force if the door contact sensor was bypassed. 
*Rationale:* The bot's intelligence is limited to the accuracy of the underlying sensor data.

### 3.3 No Predictive Analytics or Hardware Forecasting
**Limitation:** SAI reports current state and historical logs.
**What We Cannot Resolve:**
- The bot **cannot** predict exactly when a UPS battery will permanently fail in the future.
- The bot **cannot** forecast network outages or predict hardware lifecycles.
*Rationale:* While the bot can report *"Battery voltage is low (11.2V)"*, it lacks the complex machine-learning models required for predictive maintenance forecasting.

### 3.4 No Real-Time Video Streaming
**Limitation:** SAI processes metadata, not media.
**What We Cannot Resolve:**
- The bot **cannot** display the live video feed of a CCTV camera in the chat window.
- The bot **cannot** perform facial recognition or describe what is happening in a video feed.
*Rationale:* Transmitting and analyzing raw video streams falls outside the scope of a text-based telemetry diagnostic assistant and violates strict privacy and bandwidth constraints.

### 3.5 No Complex Arbitrary Statistical Modeling
**Limitation:** Queries must map to supported intent handlers.
**What We Cannot Resolve:**
- The bot **cannot** execute complex, multi-variate statistical queries on the fly (e.g., *"Show me a scatter plot correlation between temperature spikes and camera disconnects over the last 3 years"*).
*Rationale:* The system is optimized for instantaneous operational health checks, not deep exploratory data science.

---

## 4. Target User Personas & Primary Use Cases

| Persona | Role & Objectives | Typical Workflows & Queries |
| :--- | :--- | :--- |
| **Security Operations Center (SOC) Analysts** | Continuous 24/7 monitoring of active security threats, door intrusions, and camera outages. | *"List all active intrusion alarms in the North Region."*<br>*"Which camera channels are offline at Branch X?"* |
| **Facility & Branch Managers** | Daily operational upkeep, power supply verification, UPS battery health checks, HVAC monitoring. | *"Is the branch currently running on generator or mains power?"*<br>*"What is the battery voltage?"* |
| **Field Maintenance Technicians** | On-site servicing, hardware replacements, pre-visit diagnostic troubleshooting. | *"What is the HDD error status for Branch X?"*<br>*"Are there any offline devices I should look at?"* |
| **Regional Operations Leadership** | Executive oversight of branch uptime, fleet health metrics, and regional compliance. | *"Show me a fleet overview."*<br>*"How many branches in my zone are currently offline?"* |

---

## 5. Core Functional Requirements & Capabilities

### 5.1 Conversational Intelligence & Intent Resolution

#### FR-1.1: Plain-English Query Processing
- The system shall accept unformatted, natural-language text inputs.
- The system shall classify incoming queries into one of **18+ specialized domain intent categories**.

#### FR-1.2: Fuzzy Branch Name Resolution
- Incorporates a fuzzy string matching algorithm to resolve branch names despite typos, abbreviations, or inconsistent capitalization (e.g., `"bally bzr"` $\rightarrow$ `"BALLY BAZAR"`).
- Automatically strips organizational prefixes (`"BRANCH "`, `"BOI-"`) during resolution and display.

#### FR-1.3: Ambiguity Filtering & Clarification
- If a domain query (*"What is the power status?"*) lacks a target location and cannot be inferred from session memory, the system MUST pause data fetching and prompt the user for clarification (*"Please specify which branch you are asking about."*).

#### FR-1.4: Contextual Memory & Topic Retention
- Maintains a rolling session context. If a user asks *"What is the CCTV status of Branch X?"* and follows up with *"And the power?"*, the system maps the new intent (Power) to the retained context (Branch X).

---

### 5.2 Truth-Augmented Deterministic Answer Pipeline

#### FR-2.1: Context-Augmented Generation (CAG) Architecture
- To absolutely prevent AI hallucination, the system MUST retrieve deterministic data from the live state database.
- The AI is then restricted to summarizing and formatting this verified data block. The AI is **never** permitted to guess a device state.

#### FR-2.2: Stale Data Detection
- All telemetry snapshots include a "data as of" timestamp.
- Data exceeding a freshness threshold (default: 10 minutes) MUST trigger a **Stale Data Warning** in the final response.

---

### 5.3 Multi-Tenant Access Control & Scope Filtering

#### FR-3.1: Strict Tenant Isolation (Fail-Closed)
- Every query MUST carry an authenticated identity claim linking the user to a specific Tenant.
- Unrecognized or malformed identities result in immediate rejection (Fail-Closed) with zero data returned.

#### FR-3.2: Hierarchical Node Scoping
- The hierarchy is enforced strictly: **Head Office $\rightarrow$ Region $\rightarrow$ Zone $\rightarrow$ Branch**.
- A Zone Manager cannot query a branch in a different Zone. The system will return an "Out of Scope / Access Denied" response, actively preventing horizontal data leakage.

---

## 6. Detailed Domain Query Handlers (The 18 Intents)

### 6.1 Security & Video
- **CCTV Monitoring:** Reports total cameras, online/offline counts, identifies offline channels, and groups contiguous failed channels (e.g., *Channels 7-9*).
- **Storage & Recording:** Checks CCTV Hard Disk Drive (HDD) status (Healthy, Error, Unformatted) and active recording state.
- **Alarm Tracking:** Lists unacknowledged security alarms (motion, glass break, panic) and historical alert logs.
- **Door Security & Access Control:** Reports vault/entrance time-lock states (Locked, Forced Open), access card user counts, and tamper alerts.

### 6.2 Infrastructure & Connectivity
- **Power & Energy:** Monitors Mains AC power vs. Generator, UPS status, battery voltage, and triggers low-battery warnings.
- **Gateway & Network:** Tracks the primary IoT gateway health, active SIM operator, signal strength (RSSI/RSRP), and IP connectivity.
- **Subsystem Diagnostics:** Monitors secondary systems (fire panels, HVAC, leak detectors) and provides root-cause summaries for compound faults.

### 6.3 Asset & Fleet Management
- **Device Inventory:** Full audit of monitored devices at a location, filtered by active/offline state, including model and serial numbers.
- **Fleet Analytics:** Network-wide summaries (*"Total: 85 Online | 15 Offline"*), multi-branch comparisons, and enterprise-wide inactive branch reports.
- **Hierarchy Navigation:** Resolves organizational structures (*"List all branches in the West Region"*).

---

## 7. Response Standardization & Canonical Formatting Rules

To ensure a professional and uniform user experience, the AI's output is strictly constrained by format contracts:

### 7.1 Canonical Answer Templates
- **Single-Branch Output MUST match:**
  $$\text{**For Branch [BRANCH\_NAME], the [Metric Label] is [Value].**}$$
  *(e.g., `**For Branch BALLY BAZAR, the Gateway status is ONLINE.**`)*
- **Multi-Branch / Fleet Output MUST match:**
  $$\text{**Total: X Online | Y Offline**}$$
  *(Individual branch headers are prohibited in fleet roll-ups).*

### 7.2 Strict Null & Missing Value Mapping
- Raw telemetry values of `null`, `N/A`, or empty strings are **strictly prohibited** from reaching the user.
- The system must map these to logical states:
  - Missing device telemetry $\rightarrow$ `"Offline"`
  - Missing optional component $\rightarrow$ `"Not Installed"`
  - Missing numeric metric $\rightarrow$ `"Unavailable"`

### 7.3 Channel Range Grouping Algorithm
- Consecutively numbered offline devices of the same model MUST be grouped to prevent log spam.
- **Prohibited:** `Channel 5 offline, Channel 6 offline, Channel 7 offline`
- **Required:** `Channels 5–7 (3 units offline)`

---

## 8. Data Ingestion & State Pipeline

### 8.1 Ingestion Security & Integrity
- **Webhook HMAC Authentication:** All incoming event payloads MUST be cryptographically verified using HMAC-SHA256 signatures.
- **Replay Window Safeguard:** Events with timestamps outside a $\pm 5$ minute window MUST be rejected to prevent replay attacks.
- **Idempotency:** Processing enforces strict deduplication. Events are marked "processed" only after both historical logging and live state caches are successfully updated.

### 8.2 Dual-Layer Storage Strategy
- **Live State Repository (Memory Cache):** Maintains high-speed snapshots of the latest telemetry state for sub-millisecond chat retrieval.
- **Historical Event Log (Time-Series DB):** Append-only, partitioned log (180-day retention) for auditing, disconnect history, and complete system replays.

---

## 9. Security, Privacy & Application Safeguards

- **SSRF Guard (Server-Side Request Forgery):** Validates all client-provided host references against a strict allowlist using exact domain parsing (preventing substring bypasses).
- **Prompt Injection Defense:** User inputs are enclosed in explicit delimiters (`<<<USER_QUESTION>>>`) instructing the AI to treat the contents strictly as data, neutralizing jailbreak attempts.
- **CSP Framing Protection:** Emits `Content-Security-Policy: frame-ancestors` headers, ensuring the chat widget can only be embedded inside authorized corporate dashboards.

---

## 10. User Interface (UI) & UX Specifications

- **Seamless Iframe Widget:** Renders as a floating chat panel embedded directly within existing enterprise IoT dashboards.
- **Responsive Scaling:** UI components (input box, message bubbles, fonts) automatically scale and adapt from mobile displays up to 4K desktop monitors.
- **Real-Time Token Streaming:** Simulates human typing by streaming responses character-by-character, drastically reducing perceived latency.
- **Interactive Suggestion Chips:** Dynamically generates 2–4 context-aware follow-up prompts after every response (e.g., `[Check Door Status]`, `[View Offline Cameras]`), enabling one-click drill-downs.

---

## 11. Non-Functional Requirements (NFRs)

- **Performance:** Live query deterministic data retrieval and prompt assembly MUST complete in $< 300\text{ ms}$.
- **Streaming Latency:** Initial token display on the UI MUST commence within $< 1.5\text{ seconds}$ of query submission.
- **Reliability:** The ingestion pipeline MUST achieve 99.99% event delivery via Dead-Letter Queues (DLQ) and automatic retries. No data can be silently dropped.
- **Graceful Degradation:** If the external LLM provider experiences an outage, the system MUST bypass the AI and render the deterministic data directly via pre-built fallback text templates.

---
*End of Product Requirements Document (PRD)*
