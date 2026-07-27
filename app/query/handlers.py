import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import select

from app.auth.scope_resolver import resolved_scope
from app.clients.thingsboard import UserAwareThingsBoardClient, require_uuid
from app.config import Settings
from app.db.models import DeviceEvent
from app.hierarchy.scope import ScopedBranches
from app.normalization import build_snapshot
from app.normalization.snapshot import BranchSnapshot
from app.query import cctv
from app.query.answer_support import (
    LADDER_KEYS,
    first_non_blank,
    resolve_battery_status,
    resolve_boolean,
    resolve_subsystem_alarm,
    resolve_subsystem_fault,
)
from app.query.contracts import Answer, ExtractedIntent, RequestContext
from app.query.key_profiles import keys_for
from app.tasks.live_sync import load_fleet_states

logger = logging.getLogger(__name__)

# Callable that resolves the caller's authorized branch set. Injectable so handlers
# are unit-testable without a live DB/Redis.
ScopeFn = Callable[[RequestContext], Awaitable[ScopedBranches]]


async def _default_scope(ctx: RequestContext) -> ScopedBranches:
    """Chat's scope, from the same resolver the HTTP endpoints use.

    This deliberately does NOT call branch_scope() directly. It used to, which meant
    the chat path and app/deps.py built the same security boundary twice — so a fix
    to one silently missed the other. PermissionCheckUnavailable propagates to the
    orchestrator, which turns it into a refusal message.
    """
    return await resolved_scope(ctx.db, ctx.redis, ctx.tenant, ctx.tb.settings)


class GlobalOverview:
    """Fleet overview, answered from the caller's SCOPED hierarchy set — never the
    raw ThingsBoard inventory. Counting live TB devices with the service token would
    leak every region of the customer to a region-scoped caller. When the scheduled
    live sync has populated fleet snapshots, the answer adds real online/offline
    counts (computed over the scoped devices only)."""

    intent = "global_overview"

    def __init__(self, scope_fn: ScopeFn = _default_scope) -> None:
        self._scope_fn = scope_fn

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.prefix:
            return Answer(
                "Your token is not mapped to a customer, so I cannot retrieve fleet data."
            )
        scoped = await self._scope_fn(ctx)
        count = len(scoped.tb_device_ids)
        states = await load_fleet_states(ctx.redis, ctx.tenant.prefix, scoped.tb_device_ids)
        if not states:
            return Answer(
                f"You have {count} device(s) in your authorized scope.",
                {"device_count": count},
                [{"type": "hierarchy", "resource": "scoped-branches"}],
            )
        tally = Counter(build_snapshot(raw).gateway.state.value for raw in states.values())
        online = tally.get("ONLINE", 0)
        offline = tally.get("OFFLINE", 0)
        other = len(states) - online - offline
        text = (
            f"You have {count} device(s) in your authorized scope: "
            f"{online} online, {offline} offline"
        )
        if other:
            text += f", {other} in other states"
        if count > len(states):
            text += f" ({count - len(states)} with no recent data)"
        return Answer(
            text + ".",
            {
                "device_count": count,
                "online": online,
                "offline": offline,
                "other": other,
                "no_data": count - len(states),
            },
            [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
        )


class DeviceInventory:
    """Device list, scoped to the caller's hierarchy. Leaf node_id == the branch/device
    name, so branch_node_ids is the authorized name list — no TB call needed."""

    intent = "device_inventory"

    def __init__(self, scope_fn: ScopeFn = _default_scope) -> None:
        self._scope_fn = scope_fn

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.prefix:
            return Answer(
                "Your token is not mapped to a customer, so I cannot retrieve device inventory."
            )
        scoped = await self._scope_fn(ctx)
        names = scoped.branch_node_ids
        shown = ", ".join(names[:10]) or "none"
        suffix = " (showing first 10)" if len(names) > 10 else ""
        return Answer(
            f"You have {len(names)} branch device(s) in scope: {shown}{suffix}.",
            {"devices": names},
            [{"type": "hierarchy", "resource": "scoped-branches"}],
        )


class AlarmDetail:
    intent = "alarm_detail"

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        rows = (
            (
                await ctx.db.execute(
                    select(DeviceEvent.event_type)
                    .where(
                        DeviceEvent.tenant_id == ctx.tenant.tenant_id,
                        DeviceEvent.event_type.in_(["alarm", "alert", "fault"]),
                    )
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        counts = Counter(rows)
        if not counts:
            return Answer("I found no recorded alarm events for this tenant.", {"alarms": {}})
        summary = ", ".join(f"{kind}: {count}" for kind, count in counts.items())
        return Answer(
            f"Recorded alarms: {summary}.",
            {"alarms": dict(counts)},
            [{"type": "device_event", "resource": "tenant-scoped"}],
        )


# --- metric handler ----------------------------------------------------------

# Intents answered from a normalized BranchSnapshot field. Only intents the snapshot
# actually models are here; network/door/access-control/fault-reason have no snapshot
# field yet, so they are intentionally absent (add when the snapshot grows those).
METRIC_INTENTS = frozenset(
    {
        "gateway_status",
        "battery_voltage",
        "battery_health",
        "battery_low_status",
        "ac_voltage",
        "system_current",
        "power_status",
        "cctv_status",
        "cctv_hdd_error_status",
        "cctv_hdd_info",
        "cctv_device_info",
        "cctv_recording_info",
        "device_hardware",
        "subsystem_status",
    }
)


class _TbClient(Protocol):
    async def attributes(self, device_id: str, scope: str) -> Any: ...
    async def telemetry(self, device_id: str, keys: str | None = ...) -> Any: ...
    async def close(self) -> None: ...


async def _load_raw(client: _TbClient, device_id: str, keys: list[str]) -> dict[str, Any]:
    """Assemble a flat {key: value} dict from TB attributes + telemetry.

    ALL server + client attributes are fetched (that endpoint returns every key), so
    attribute-typed real keys — ticketStatus_*, iasBasFasStatus_*, statusbox_* — are
    always present. Telemetry is fetched for an EXPLICIT keys list (the intent's profile
    plus every answer-layer ladder key), so telemetry-typed fault/count keys are imported
    too, and the request always carries `keys` (some TB versions require it). Attributes
    first, then telemetry — a live telemetry value wins over a stale attribute.
    """
    raw: dict[str, Any] = {}
    for scope in ("SERVER_SCOPE", "CLIENT_SCOPE"):
        attrs = await client.attributes(device_id, scope)
        if isinstance(attrs, list):
            for item in attrs:
                if isinstance(item, dict) and "key" in item:
                    raw[str(item["key"])] = item.get("value")
    series = await client.telemetry(device_id, keys=",".join(keys) if keys else None)
    if isinstance(series, dict):
        for key, points in series.items():
            if isinstance(points, list) and points and isinstance(points[0], dict):
                raw[str(key)] = points[0].get("value")
    return raw


class MetricHandler:
    """Deterministic per-metric answers from a normalized snapshot.

    SECURITY: the chat path fetches via ThingsBoard using the CALLER's token
    (UserAwareThingsBoardClient), and only after the device is confirmed to be in
    the caller's regional scope. That is two gates — our scope check AND TB's own
    ACL — so a scope-check bug alone cannot leak cross-tenant data.
    """

    def __init__(
        self,
        scope_fn: ScopeFn = _default_scope,
        client_factory: Callable[[Settings, str], _TbClient] = UserAwareThingsBoardClient,
    ) -> None:
        self._scope_fn = scope_fn
        self._client_factory = client_factory

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name in METRIC_INTENTS

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        device_id = intent.device_id
        if not device_id:
            return Answer(
                "Name a device to check — for example, 'battery voltage of device <uuid>'."
            )
        try:
            require_uuid(device_id, "device_id")
        except ValueError:
            return Answer(f"'{device_id}' is not a valid device id.")
        if not ctx.tenant.prefix:
            return Answer("Your token is not mapped to a customer, so I cannot scope device data.")

        scoped = await self._scope_fn(ctx)
        if not scoped.tb_device_ids:
            return Answer(
                "No branches are imported for your scope yet — run the hierarchy import first."
            )
        if device_id not in scoped.tb_device_ids:
            return Answer("That device is not in your authorized scope.")
        if not ctx.tenant.user_token:
            return Answer("A user token is required to read device data.")

        # Intent's key profile + every answer-layer ladder key, so nothing under-imports.
        key_set = set(keys_for(intent.name)) | LADDER_KEYS
        if intent.name.startswith("cctv"):
            key_set |= cctv.CCTV_KEYS
        keys = sorted(key_set)
        client = self._client_factory(ctx.tb.settings, ctx.tenant.user_token)
        try:
            raw = await _load_raw(client, device_id, keys)
        except Exception as exc:
            # A ThingsBoard failure (expired/invalid caller token, TB down) must read as
            # an answer, not a 500 through the chat pipeline.
            logger.warning("device fetch failed for %s", device_id, exc_info=True)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                return Answer(
                    "ThingsBoard rejected your token for that device — it may have expired. "
                    "Sign in again and retry.",
                    {"error": "thingsboard_auth", "device_id": device_id},
                )
            return Answer(
                "I could not reach ThingsBoard for that device just now. Please retry.",
                {"error": "thingsboard_unavailable", "device_id": device_id},
            )
        finally:
            await client.close()
        raw.setdefault("device_id", device_id)

        return _format_metric(intent, build_snapshot(raw), device_id)


def _source(device_id: str) -> list[dict[str, str]]:
    return [{"type": "thingsboard", "resource": f"device:{device_id}"}]


def _format_metric(intent: ExtractedIntent, snap: BranchSnapshot, device_id: str) -> Answer:
    name = intent.name
    src = _source(device_id)

    if name == "gateway_status":
        g = snap.gateway
        return Answer(f"Gateway is {g.state.value}.", {"gateway_state": g.state.value}, src)

    if name == "battery_voltage":
        p = snap.power
        if p.battery_voltage is None:
            return Answer("No battery voltage is being reported for this device.", {}, src)
        return Answer(
            f"Battery voltage is {p.battery_voltage} (source: {p.battery_voltage_source}).",
            {"battery_voltage": p.battery_voltage, "battery_low": p.battery_low},
            src,
        )

    if name == "battery_health":
        p = snap.power
        status = resolve_battery_status(snap.raw_data)
        volt = "not reported" if p.battery_voltage is None else str(p.battery_voltage)
        return Answer(
            f"Battery health — status {status}, voltage {volt}.",
            {"battery_status": status, "battery_voltage": p.battery_voltage},
            src,
        )

    if name == "battery_low_status":
        status = resolve_battery_status(snap.raw_data)
        return Answer(
            f"Battery status: {status}.",
            {"battery_status": status, "battery_low": snap.power.battery_low},
            src,
        )

    if name == "ac_voltage":
        v = snap.power.ac_voltage
        text = "No AC voltage is being reported." if v is None else f"AC voltage is {v}."
        return Answer(text, {"ac_voltage": v}, src)

    if name == "system_current":
        v = snap.power.system_current
        text = "No system current is being reported." if v is None else f"System current is {v}."
        return Answer(text, {"system_current": v}, src)

    if name == "power_status":
        p = snap.power
        return Answer(
            f"Power — battery {p.battery_voltage}, AC {p.ac_voltage}, "
            f"mains on {p.mains_on}, battery low {p.battery_low}.",
            {
                "battery_voltage": p.battery_voltage,
                "ac_voltage": p.ac_voltage,
                "mains_on": p.mains_on,
                "battery_low": p.battery_low,
            },
            src,
        )

    if name == "cctv_status":
        c = snap.cctv
        if c.camera_count is None:
            return Answer(
                f"CCTV status is {c.state.value}; camera count is not reported.",
                {"cctv_state": c.state.value},
                src,
            )
        return Answer(
            f"CCTV status is {c.state.value}; {c.online_camera_count}/{c.camera_count} "
            "cameras online.",
            {"cctv_state": c.state.value, "online": c.online_camera_count, "total": c.camera_count},
            src,
        )

    if name == "cctv_hdd_error_status":
        raw = snap.raw_data
        err = resolve_boolean(raw, "HDD ERROR", "ticketStatus_HDD_ERROR", "cameraStatus_HDD ERROR")
        if err is None:
            health = first_non_blank(raw, "hddStatus")
            if health is not None and health.upper() == "HEALTHY":
                err = False
        state = "ACTIVE" if err is True else "NORMAL" if err is False else "N/A"
        return Answer(f"CCTV HDD error status: {state}.", {"hdd_error": err, "state": state}, src)

    if name == "cctv_hdd_info":
        slots = cctv.hdd_info(snap.raw_data)
        if not slots:
            return Answer("CCTV HDD information is not available.", {"hdd_slots": []}, src)
        lines = [
            f"Slot {s['slot']}: {s['status']}, Capacity {s['capacity_tb']} TB, Free {s['free_tb']} TB"
            for s in slots
        ]
        return Answer(
            "CCTV HDD information — " + "; ".join(lines) + ".", {"hdd_slots": slots}, src
        )

    if name == "cctv_device_info":
        info = cctv.device_info(snap.raw_data)
        if not info:
            return Answer("CCTV device information is not available.", {"device_info": {}}, src)
        labels = {
            "vendor": "Vendor",
            "model": "Model",
            "hdd_slots": "HDD Slots",
            "storage_tb": "Storage (TB)",
            "resolution": "Resolution",
        }
        parts = [f"{labels[k]}: {info[k]}" for k in labels if k in info]
        return Answer("CCTV device info — " + ", ".join(parts) + ".", {"device_info": info}, src)

    if name == "cctv_recording_info":
        rec = cctv.recording_summary(snap.raw_data)
        if not rec["available"]:
            return Answer("CCTV recording information is not available.", {"recording": rec}, src)
        text = (
            f"CCTV recording (retention target {rec['retention_days']}d): {rec['total']} camera(s) "
            f"— {rec['compliant']} compliant, {rec['non_compliant']} non-compliant"
        )
        if rec["zero"] > 0:
            text += f", {rec['zero']} with 0 days (channel(s) {', '.join(rec['zero_channels'])})"
        text += f". Recorded-days range {rec['min_days']}–{rec['max_days']}."
        return Answer(text, {"recording": rec}, src)

    if name == "device_hardware":
        h = snap.hardware
        return Answer(
            f"Hardware — CPU {h.cpu}, memory {h.memory}, disk {h.disk}, "
            f"temperature {h.temperature}.",
            {"cpu": h.cpu, "memory": h.memory, "disk": h.disk, "temperature": h.temperature},
            src,
        )

    if name == "subsystem_status":
        return _format_subsystem(intent, snap, src)

    # METRIC_INTENTS and this dispatch must stay in sync; this is the guard if they drift.
    return Answer("I could not map that metric to a device field.", {}, src)


def _format_subsystem(
    intent: ExtractedIntent, snap: BranchSnapshot, src: list[dict[str, str]]
) -> Answer:
    # key -> (SubsystemStatus, AnswerSupport target name for the fault/alarm ladders)
    by_name = {
        "cctv": (snap.subsystems.cctv, "cctv"),
        "ias": (snap.subsystems.ias, "ias"),
        "bas": (snap.subsystems.bas, "bas"),
        "fas": (snap.subsystems.fas, "fas"),
        "timelock": (snap.subsystems.time_lock, "timeLock"),
        "accesscontrol": (snap.subsystems.access_control, "accessControl"),
    }
    key = (intent.subsystem or "").lower().replace("_", "").replace(" ", "")
    entry = by_name.get(key)
    if entry is not None:
        one, target = entry
        # Enrich the _sts state with fault/alarm resolved from the real fleet keys.
        fault = resolve_subsystem_fault(snap.raw_data, target)
        alarm = resolve_subsystem_alarm(snap.raw_data, target)
        parts = [f"{one.system_name} is {one.state.value}"]
        if fault is not None:
            parts.append(f"fault {'YES' if fault else 'no'}")
        if alarm is not None:
            parts.append(f"alarm {'YES' if alarm else 'no'}")
        return Answer(
            ", ".join(parts) + ".",
            {"subsystem": one.system_name, "state": one.state.value, "fault": fault, "alarm": alarm},
            src,
        )
    states = {s.system_name: s.state.value for s, _ in by_name.values()}
    summary = ", ".join(f"{n}: {st}" for n, st in states.items())
    return Answer(f"Subsystem status — {summary}.", {"subsystems": states}, src)
