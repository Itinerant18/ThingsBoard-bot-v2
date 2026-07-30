import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Protocol

from app.auth.scope_resolver import resolved_scope
from app.auth.tb_acl import PermissionCheckUnavailable, SessionExpired, caller_identity
from app.clients.thingsboard import UserAwareThingsBoardClient
from app.config import Settings
from app.hierarchy.scope import ScopedBranches
from app.normalization import build_snapshot
from app.normalization.flatten import expand_containers, request_keys
from app.normalization.snapshot import BranchSnapshot
from app.query import cctv, derived, history
from app.query.alarm_answers import AlarmRecord, format_alarm_answer, normalize_alarm
from app.query.answer_support import (
    LADDER_KEYS,
    first_non_blank,
    resolve_battery_status,
    resolve_boolean,
    resolve_subsystem_alarm,
    resolve_subsystem_fault,
)
from app.query.audit import (
    AuditScope,
    filter_entries,
    format_audit_answer,
    normalize_entries,
    window_bounds,
)
from app.query.cctv_fleet import aggregate_cctv, format_cctv_fleet, rank_cctv_branches
from app.query.contracts import Answer, ExtractedIntent, RequestContext
from app.query.disclosure import REFUSAL
from app.query.fleet_health import (
    _LISTING_RE,
    aggregate_fleet_health,
    category_listing,
    format_fleet_health,
    normalize_category,
    rank_branches,
)
from app.query.hierarchy_answers import (
    area_device_filter,
    format_hierarchy_answer,
    load_scoped_tree,
)
from app.query.key_profiles import keys_for
from app.query.users import format_user_answer, normalize_users
from app.query.uuids import is_uuid as _is_uuid
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


def _requested_device(intent: ExtractedIntent, scoped_ids: list[str]) -> tuple[str | None, bool]:
    """(device_id, refuse) for a fleet handler.

    A device_id that is not a UUID did not come from the caller naming a device — it
    is a word the extractor scraped after "device", as in "device category". Ignore
    it and answer fleet-wide rather than refusing.
    """
    requested = intent.device_id
    if not requested or not _is_uuid(requested):
        return None, False
    return (requested, False) if requested in scoped_ids else (None, True)


def _name_the_branch(answer: Answer, intent: ExtractedIntent) -> Answer:
    """Prefix a per-device answer with the branch the question named.

    "What is the status of CCTV channel 15 at BALLYBAZAR?" answered "CCTV status is
    FAULT; 16/16 cameras online." The device WAS resolved correctly — the answer just
    never said which one, so it is indistinguishable from a fleet-wide reply. Same
    reasoning as _scoped_to, which the fleet handlers already use and MetricHandler
    never did.
    """
    if not intent.node_name or answer.structured.get("error"):
        return answer
    if intent.node_name.lower() in answer.text.lower():
        return answer
    answer.text = f"{intent.node_name} — {answer.text}"
    answer.structured.setdefault("scoped_to", intent.node_name)
    return answer


_ASKS_HIERARCHY = re.compile(
    r"\bzones?\b|\bregions?\b|\bnbg\b|\bfgmo\b|\bcircles?\b|\bhierarch|\bbelongs? to\b"
    r"|\bsub-?areas?\b"
)
# "branch" alone is not enough — "battery voltage of Liluah branch" is a metric
# question. It counts only when the question is also asking to count or to list.
_BRANCH_LISTING = re.compile(
    r"\bbranch(?:es)?\b.*\b(?:how many|list|all|count|per|each|total)\b"
    r"|\b(?:how many|list|all|count|per|each|total)\b.*\bbranch(?:es)?\b"
)


async def _hierarchy_answer(
    intent: ExtractedIntent, ctx: RequestContext, scoped: ScopedBranches
) -> Answer | None:
    """format_hierarchy_answer's reply, when the question is really about structure.

    Zone and region questions reach three different handlers depending on how the
    extractor classifies them — "how many branches under the WEST II zone" landed on
    GlobalOverview and got a device count, "list all branches" landed on
    DeviceInventory and got the 98-name dump. The formatter could answer both all
    along. Rather than teach each handler the hierarchy, they all ask here first.
    """
    if ctx.db is None or not ctx.tenant.prefix:
        return None
    question = intent.raw_question.lower()
    if not (_ASKS_HIERARCHY.search(question) or _BRANCH_LISTING.search(question)):
        return None
    tree = await load_scoped_tree(
        ctx.db, ctx.tenant.prefix, scoped.branch_node_ids, scoped.tb_device_ids
    )
    if not tree.nodes:
        return None
    text, structured = format_hierarchy_answer(tree, intent.raw_question)
    return Answer(text, structured, [{"type": "hierarchy", "resource": "scoped-branches"}])


async def _category_listing(
    intent: ExtractedIntent, ctx: RequestContext, scoped: ScopedBranches
) -> Answer | None:
    """"Show me all IAS devices" — the branches where one subsystem is deployed.

    Reached from the inventory handlers, which is where the extractor sends these.
    Guarded on the question naming a subsystem AND asking to list, so the fleet-state
    read stays off the path of every other inventory question.
    """
    question = intent.raw_question.lower()
    if not ctx.tenant.prefix:
        return None
    if normalize_category(None, question) is None or not _LISTING_RE.search(question):
        return None
    states = await load_fleet_states(ctx.redis, ctx.tenant.prefix, scoped.tb_device_ids)
    if not states:
        return None
    snapshots = {device_id: build_snapshot(raw) for device_id, raw in states.items()}
    listed = category_listing(
        aggregate_fleet_health(snapshots, scoped.tb_device_ids), intent.raw_question
    )
    if listed is None:
        return None
    text, rows = listed
    return Answer(
        text,
        {"category_branches": rows[:50], "count": len(rows)},
        [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
    )


_ASKS_GEO = re.compile(
    r"\bmap\b|\blatitude\b|\blongitude\b|\bcoordinates?\b|\bgeograph"
    r"|\bwhere are\b.*\blocated\b|\blocated\b.*\bgeograph"
)
_ASKS_COORDS = re.compile(r"\blatitude\b|\blongitude\b|\bcoordinates?\b")
# "How many devices are at each branch?" and "show me the branch report" want a
# number PER branch. The hierarchy answered with a count OF branches.
_ASKS_PER_BRANCH = re.compile(
    r"\b(?:how many|number of|count)\b.*\bdevices?\b.*\b(?:each|per|every)\b.*\bbranch"
    r"|\bdevices?\b.*\b(?:per|each)\b.*\bbranch"
    r"|\bbranch report\b|\bper-?branch (?:report|summary|breakdown)\b"
)


async def _per_branch_counts(
    intent: ExtractedIntent, ctx: RequestContext, scoped: ScopedBranches
) -> Answer | None:
    """Modules deployed at each branch.

    aggregate_fleet_health already walks every branch and records its modules while
    summing them, so this is the same read the fleet answers make - no extra call.
    """
    if not ctx.tenant.prefix or not _ASKS_PER_BRANCH.search(intent.raw_question.lower()):
        return None
    states = await load_fleet_states(ctx.redis, ctx.tenant.prefix, scoped.tb_device_ids)
    if not states:
        return None
    snapshots = {device_id: build_snapshot(raw) for device_id, raw in states.items()}
    summary = aggregate_fleet_health(snapshots, scoped.tb_device_ids)
    counts = [(branch, len(modules)) for branch, modules in summary.branches.items()]
    counts.sort(key=lambda pair: (-pair[1], pair[0]))
    rows = [{"branch": branch, "modules": modules} for branch, modules in counts]
    if not rows:
        return None
    shown = ", ".join(f"{r['branch']}: {r['modules']}" for r in rows[:20])
    more = f" (showing first 20 of {len(rows)})" if len(rows) > 20 else ""
    return Answer(
        f"Modules deployed per branch: {shown}{more}.",
        {"per_branch_modules": rows},
        [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
    )


def _scoped_to(intent: ExtractedIntent, requested: str | None, area_name: str | None) -> str | None:
    """The place an answer was narrowed to, for echoing back to the caller.

    An answer that silently applied a filter is indistinguishable from one that
    ignored it.
    """
    if area_name:
        return area_name
    if requested and intent.node_name:
        return intent.node_name
    return None


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
        per_branch = await _per_branch_counts(intent, ctx, scoped)
        if per_branch is not None:
            return per_branch
        hierarchy = await _hierarchy_answer(intent, ctx, scoped)
        if hierarchy is not None:
            return hierarchy
        listing = await _category_listing(intent, ctx, scoped)
        if listing is not None:
            return listing
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
    name, so branch_node_ids is the authorized name list — no TB call needed.

    That last clause was only true after resolved_scope started applying ThingsBoard's
    ACL to branch_node_ids as well. Before that it named 104 branches to a caller
    ThingsBoard authorized for 100.
    """

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
        question = intent.raw_question.lower()
        # The gate already resolved a branch name out of the question and the
        # orchestrator put it on the intent — this handler was throwing it away and
        # printing the whole inventory. "Is there a NASIK branch currently active?"
        # answered with 104 branch names, NASIK among them.
        # Membership is tested on tb_device_ids because that is the ACL-filtered list.
        if intent.node_name and intent.device_id in set(scoped.tb_device_ids):
            return Answer(
                f"Yes — {intent.node_name} is one of the {len(scoped.tb_device_ids)} "
                "branches in your authorized scope.",
                {
                    "branch": intent.node_name,
                    "device_id": intent.device_id,
                    "in_scope": True,
                },
                [{"type": "hierarchy", "resource": "scoped-branches"}],
            )
        if "region" in question and "active" in question:
            if ctx.tenant.region:
                return Answer(
                    f"One region is active in your current scope: {ctx.tenant.region}.",
                    {"active_regions": [ctx.tenant.region], "count": 1},
                    [{"type": "authorization-scope", "resource": "current-user"}],
                )
            return Answer(
                "No single region is selected; your current scope is customer-wide.",
                {"active_regions": [], "count": 0, "customer_wide": True},
                [{"type": "authorization-scope", "resource": "current-user"}],
            )
        # Widened beyond the literal word "map": "what is the latitude and longitude
        # for each branch" and "where are the branches located geographically" both
        # went to the hierarchy summary instead, which names no coordinate at all.
        if _ASKS_GEO.search(question):
            states = await load_fleet_states(ctx.redis, ctx.tenant.prefix, scoped.tb_device_ids)
            markers = []
            for device_id, raw in states.items():
                lat = first_non_blank(raw, "lat1", "lat")
                lon = first_non_blank(raw, "lon1", "lon")
                try:
                    latitude = float(lat) if lat is not None else None
                    longitude = float(lon) if lon is not None else None
                except (TypeError, ValueError):
                    continue
                if latitude is None or longitude is None:
                    continue
                snapshot = build_snapshot(raw)
                markers.append(
                    {
                        "device_id": device_id,
                        "branch": snapshot.identity.branch_name
                        or snapshot.identity.technical_id
                        or device_id,
                        "latitude": latitude,
                        "longitude": longitude,
                        "status": snapshot.gateway.state.value,
                    }
                )
            if not markers:
                return Answer(
                    "No branch with current map coordinates is visible in your authorized scope.",
                    {"map_markers": []},
                    [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
                )
            suffix = " (showing first 20)" if len(markers) > 20 else ""
            if _ASKS_COORDS.search(question):
                listed = "; ".join(
                    f"{m['branch']} {m['latitude']}, {m['longitude']}" for m in markers[:20]
                )
                return Answer(
                    f"Coordinates for {len(markers)} branch(es): {listed}{suffix}.",
                    {"map_markers": markers},
                    [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
                )
            summary = ", ".join(
                f"{marker['branch']} ({marker['status']})" for marker in markers[:20]
            )
            return Answer(
                f"Branches visible on the map: {summary}{suffix}.",
                {"map_markers": markers},
                [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
            )
        per_branch = await _per_branch_counts(intent, ctx, scoped)
        if per_branch is not None:
            return per_branch
        # Same delegation GlobalOverview makes: the extractor sends structure
        # questions to whichever handler it feels like, so both ask the hierarchy
        # before answering with an inventory.
        hierarchy = await _hierarchy_answer(intent, ctx, scoped)
        if hierarchy is not None:
            return hierarchy
        listing = await _category_listing(intent, ctx, scoped)
        if listing is not None:
            return listing
        names = scoped.branch_node_ids
        shown = ", ".join(names[:10]) or "none"
        suffix = " (showing first 10)" if len(names) > 10 else ""
        return Answer(
            f"You have {len(names)} branch device(s) in scope: {shown}{suffix}.",
            {"devices": names},
            [{"type": "hierarchy", "resource": "scoped-branches"}],
        )


class FleetHealth:
    """Scoped dashboard-style health across deployed device categories."""

    intent = "fleet_health"

    def __init__(self, scope_fn: ScopeFn = _default_scope) -> None:
        self._scope_fn = scope_fn

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.prefix:
            return Answer(
                "Your token is not mapped to a customer, so I cannot retrieve fleet health."
            )
        scoped = await self._scope_fn(ctx)
        device_ids = scoped.tb_device_ids
        requested, refuse = _requested_device(intent, device_ids)
        if refuse:
            return Answer("That device is not in your authorized scope.")
        if requested:
            device_ids = [requested]
        # "health status of all devices in the EAST zone" — narrow to the named area.
        # Intersected with the scope, never substituted for it.
        area_ids, area_name = await area_device_filter(
            ctx.db,
            ctx.tenant.prefix,
            scoped.branch_node_ids,
            intent.raw_question,
            scoped.tb_device_ids,
        )
        if area_ids is not None:
            allowed = set(device_ids)
            device_ids = [device_id for device_id in area_ids if device_id in allowed]
            if not device_ids:
                return Answer(
                    f"No device under {area_name} is in your authorized scope.",
                    {"area": area_name, "fleet_health": None},
                )
        states = await load_fleet_states(ctx.redis, ctx.tenant.prefix, device_ids)
        snapshots = {device_id: build_snapshot(raw) for device_id, raw in states.items()}
        summary = aggregate_fleet_health(snapshots, device_ids)
        scoped_to = _scoped_to(intent, requested, area_name)
        # "Which branch has the worst overall health?" was answered with the fleet
        # aggregate, which names no branch at all. Rank the per-branch rows the
        # aggregate was already computing and discarding. Returns None for questions
        # that are genuinely about the fleet, so the summary below still serves them.
        ranked = rank_branches(summary, intent.raw_question)
        if ranked is not None:
            sentence, rows = ranked
            if scoped_to:
                sentence = f"{scoped_to} — {sentence}"
            return Answer(
                sentence,
                {"area": area_name, "ranked_branches": rows[:10], "fleet_health": summary.to_dict()},
                [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
            )
        text = format_fleet_health(summary, intent.raw_question, intent.subsystem)
        if scoped_to:
            text = f"{scoped_to} — {text}"
        return Answer(
            text,
            {"area": area_name, "fleet_health": summary.to_dict()},
            [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
        )


class CctvFleet:
    """Recording compliance and camera inventory across every branch in scope."""

    intent = "cctv_fleet"

    def __init__(self, scope_fn: ScopeFn = _default_scope) -> None:
        self._scope_fn = scope_fn

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.prefix:
            return Answer(
                "Your token is not mapped to a customer, so I cannot retrieve CCTV reports."
            )
        scoped = await self._scope_fn(ctx)
        device_ids = scoped.tb_device_ids
        requested, refuse = _requested_device(intent, device_ids)
        if refuse:
            return Answer("That device is not in your authorized scope.")
        if requested:
            device_ids = [requested]
        states = await load_fleet_states(ctx.redis, ctx.tenant.prefix, device_ids)
        # The NVR payloads arrive as JSON container strings from Redis; the dotted
        # paths the parsers read only exist after expansion.
        expanded = {device_id: expand_containers(raw) for device_id, raw in states.items()}
        fleet = aggregate_cctv(expanded)
        # Same pattern as FleetHealth: rank the per-branch rows the fleet summary was
        # already holding, before falling through to the descriptive answer.
        ranked = rank_cctv_branches(fleet, intent.raw_question)
        if ranked is not None:
            sentence, rows = ranked
            named = _scoped_to(intent, requested, None)
            return Answer(
                f"{named} — {sentence}" if named else sentence,
                {"ranked_branches": rows},
                [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
            )
        text = format_cctv_fleet(fleet, intent.raw_question)
        scoped_to = _scoped_to(intent, requested, None)
        if scoped_to:
            text = f"{scoped_to} — {text}"
        return Answer(
            text,
            {"cctv_fleet": fleet.to_dict()},
            [{"type": "fleet-snapshot", "resource": "scoped-branches"}],
        )


class CredentialRefusal:
    """Refuses secrets outright. Deliberately the simplest handler here: it reads no
    data, calls nothing, and has no branch that could answer."""

    intent = "credential_refusal"

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        logger.info("[DISCLOSURE] refused a credential request")
        return Answer(REFUSAL, {"refused": "credentials"})


class UnavailableTelemetry:
    """Says plainly that a metric is not collected.

    Grading 769 live answers found the bot NEVER declines — asked for firmware
    versions or S-Vault disk usage it substituted some unrelated real number, or
    demanded a device id that would not have produced the data either. Deflecting is
    worse than refusing: an operator can act on "we don't collect that" and cannot
    act on a confidently wrong figure.
    """

    intent = "unavailable_telemetry"

    # The subsystem acronyms an operator asks about. Six lines beats deflecting to an
    # unrelated device count, and the mapping already exists in the extractor.
    _GLOSSARY: ClassVar[dict[str, str]] = {
        "ias": "IAS — Integrated Alarm System",
        "bas": "BAS — Burglar (Intrusion) Alarm System",
        "fas": "FAS — Fire Alarm System",
        "tls": "TLS — Time Lock System",
        "acs": "ACS — Access Control System",
        "nbg": "NBG — National Banking Group, the regional tier above a zone",
        "fgmo": "FGMO — Field General Manager's Office, used interchangeably with NBG",
        "zo": "ZO — Zonal Office",
        "boi": "BOI — Bank of India",
        "tat": "TAT — Turnaround Time, how long an alarm stayed open",
        "nvr": "NVR — Network Video Recorder",
        "dvr": "DVR — Digital Video Recorder",
    }

    # What the question asked for -> what we would need to start collecting.
    _SUBJECTS = (
        ("uptime", "per-device uptime history"),
        ("disk utilization", "S-Vault disk usage"),
        ("s-vault", "S-Vault contents"),
        ("svault", "S-Vault contents"),
        ("ingestion rate", "message ingestion rate"),
        ("address", "branch postal addresses"),
        ("pincode", "branch postal codes"),
        ("pin code", "branch postal codes"),
        ("phone", "branch phone numbers"),
        ("contact", "branch contact details"),
        ("manager", "branch manager names"),
        ("escalation matrix", "an escalation matrix"),
        ("patch level", "OS patch levels"),
    )

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        text = intent.raw_question.lower()

        if "stand for" in text or "what does" in text:
            hits = [
                meaning
                for token, meaning in self._GLOSSARY.items()
                if re.search(rf"\b{token}\b", text)
            ]
            if hits:
                return Answer("; ".join(hits) + ".", {"glossary": hits})

        if re.search(r"\bwhat should i do\b|\bprocedure for\b|\bhow do i\b", text):
            return Answer(
                "I report what the fleet is doing; I do not hold your response "
                "procedures or runbooks. I can tell you the current state — which "
                "devices are faulty or offline, which alarms are open and for how "
                "long — to inform whatever your procedure says.",
                {"unavailable": "operational runbooks"},
            )

        if re.search(r"\btrend\b|\bcompared to yesterday\b|\bover (?:the )?(?:past|last)\b", text):
            return Answer(
                "I answer on the current state, not on change over time — this build "
                "has no trend layer, so I would be inventing the comparison. Device "
                "history is being recorded, so trends are possible later; today I can "
                "give you the position right now.",
                {"unavailable": "historical trend"},
            )

        subject = next(
            (label for needle, label in self._SUBJECTS if needle in text),
            "that measurement",
        )
        return Answer(
            f"I do not hold {subject} — it is not among the telemetry this fleet "
            "publishes to ThingsBoard, so I would be guessing. I can answer on device "
            "health, CCTV recording and inventory, alarms, users, audit activity and "
            "the branch hierarchy.",
            {"unavailable": subject},
        )


class HierarchyInfo:
    """Structure of the caller's organization tree — regions, zones, branch counts.

    Built outward from the branches the caller may already read, never from every
    node carrying the customer prefix: the shape of a bank's network is itself
    information, and loading by prefix would show a region-scoped user the zones
    and branch counts of regions ThingsBoard does not authorize them for.
    """

    intent = "hierarchy_info"

    def __init__(self, scope_fn: ScopeFn = _default_scope) -> None:
        self._scope_fn = scope_fn

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.prefix:
            return Answer(
                "Your token is not mapped to a customer, so I cannot retrieve the hierarchy."
            )
        scoped = await self._scope_fn(ctx)
        tree = await load_scoped_tree(
            ctx.db, ctx.tenant.prefix, scoped.branch_node_ids, scoped.tb_device_ids
        )
        text, structured = format_hierarchy_answer(tree, intent.raw_question)
        return Answer(text, structured, [{"type": "hierarchy", "resource": "scoped-branches"}])


class UserDirectory:
    """Who is registered, under the caller's OWN customer only.

    SECURITY: the customer id comes from ThingsBoard's answer to "who is this token",
    never from the question and never from the local hierarchy. The tenant-wide user
    endpoint returns every bank's staff in one page, so a customer-scoped caller must
    never reach it — this handler is the only place that decision is made.
    """

    intent = "user_directory"

    def __init__(
        self,
        identity_fn: Callable[[RequestContext], Awaitable[Any]] | None = None,
        client_factory: Callable[[Settings, str], Any] = UserAwareThingsBoardClient,
    ) -> None:
        self._identity_fn = identity_fn or self._default_identity
        self._client_factory = client_factory

    @staticmethod
    async def _default_identity(ctx: RequestContext) -> Any:
        return await caller_identity(ctx.tb.settings, ctx.tenant.user_token or "", ctx.redis)

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.user_token:
            return Answer("A user token is required to read the user directory.")
        identity = await self._identity_fn(ctx)

        client = self._client_factory(ctx.tb.settings, ctx.tenant.user_token)
        try:
            if identity.is_tenant_admin:
                body = await client.tenant_users()
                scope_label = "this ThingsBoard tenant"
            elif identity.customer_id:
                body = await client.customer_users(identity.customer_id)
                scope_label = "your customer account"
            else:
                # Authenticated but assigned to no customer: authorized for no directory.
                return Answer(
                    "Your ThingsBoard account is not assigned to a customer, so there is "
                    "no user directory I can show you.",
                    {"scope": "none", "users": []},
                )
        finally:
            await client.close()

        rows = body.get("data", []) if isinstance(body, dict) else body
        users = normalize_users(rows if isinstance(rows, list) else [])
        text, structured = format_user_answer(users, intent.raw_question, scope_label)
        return Answer(
            text, structured, [{"type": "thingsboard-users", "resource": scope_label}]
        )


class AuditLog:
    """Audit activity, filtered down to the caller.

    ThingsBoard has no per-customer audit endpoint, so the tenant-wide stream is read
    with an administrator credential and then reduced to what the caller may see. The
    ALLOW-LIST is built from the caller's own token — their customer's user list and
    their authorized device ids — never from the administrator's view, because a
    filter built from the admin's data would leak exactly what it is meant to stop.

    Failure to build the allow-list raises PermissionCheckUnavailable, which the
    orchestrator turns into a refusal. It must never degrade into "no filter".
    """

    intent = "audit_log"

    # A question with no period gets a week — long enough to answer "recently",
    # short enough that the page cap is rarely reached.
    DEFAULT_WINDOW_HOURS = 24 * 7

    def __init__(
        self,
        identity_fn: Callable[[RequestContext], Awaitable[Any]] | None = None,
        client_factory: Callable[[Settings, str], Any] = UserAwareThingsBoardClient,
        scope_fn: ScopeFn = _default_scope,
    ) -> None:
        self._identity_fn = identity_fn or UserDirectory._default_identity
        self._client_factory = client_factory
        self._scope_fn = scope_fn

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def _caller_scope(
        self, ctx: RequestContext, identity: Any, client: Any
    ) -> AuditScope:
        """Allow-list, from the CALLER's token only."""
        try:
            body = await client.customer_users(identity.customer_id)
        except Exception as exc:
            # No allow-list means no basis to show anything. Refuse rather than
            # fall through to an unfiltered stream.
            raise PermissionCheckUnavailable("could not resolve the caller's users") from exc
        rows = body.get("data", []) if isinstance(body, dict) else body
        user_ids = {
            str((row.get("id") or {}).get("id"))
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and isinstance(row.get("id"), dict)
        }
        scoped = await self._scope_fn(ctx)
        return AuditScope(
            customer_id=identity.customer_id,
            user_ids=frozenset(user_ids),
            device_ids=frozenset(scoped.tb_device_ids),
        )

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.user_token:
            return Answer("A user token is required to read audit logs.")
        identity = await self._identity_fn(ctx)
        window = intent.window
        hours = window.hours if window is not None else self.DEFAULT_WINDOW_HOURS
        label = window.label if window is not None else "the last week"
        start_ts, end_ts = window_bounds(hours)

        if identity.is_tenant_admin:
            # A tenant admin is entitled to the whole stream; read it under their own
            # token so ThingsBoard, not this service, enforces that.
            client = self._client_factory(ctx.tb.settings, ctx.tenant.user_token)
            try:
                body = await client.audit_logs(start_ts, end_ts)
            finally:
                await client.close()
            scope = AuditScope(unrestricted=True)
            scope_label = "this ThingsBoard tenant"
        elif identity.customer_id:
            caller_client = self._client_factory(ctx.tb.settings, ctx.tenant.user_token)
            try:
                scope = await self._caller_scope(ctx, identity, caller_client)
            finally:
                await caller_client.close()
            # Only now, with the allow-list already built, read the tenant stream.
            body = await ctx.tb.audit_logs(start_ts, end_ts)
            scope_label = "your customer account"
        else:
            return Answer(
                "Your ThingsBoard account is not assigned to a customer, so there is no "
                "audit activity I can attribute to you.",
                {"scope": "none", "entries": []},
            )

        rows = body.get("data", []) if isinstance(body, dict) else body
        truncated = bool(isinstance(body, dict) and body.get("truncated"))
        visible = filter_entries(normalize_entries(rows if isinstance(rows, list) else []), scope)
        logger.info(
            "[AUDIT] scope=%s fetched=%d visible=%d",
            scope_label,
            len(rows) if isinstance(rows, list) else 0,
            len(visible),
        )
        text, structured = format_audit_answer(
            visible,
            intent.raw_question,
            scope_label,
            label,
            scope=scope,
            truncated=truncated,
        )
        return Answer(text, structured, [{"type": "thingsboard-audit", "resource": scope_label}])


class AlarmDetail:
    intent = "alarm_detail"

    def __init__(
        self,
        scope_fn: ScopeFn = _default_scope,
        client_factory: Callable[[Settings, str], Any] = UserAwareThingsBoardClient,
    ) -> None:
        self._scope_fn = scope_fn
        self._client_factory = client_factory

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return intent.name == self.intent

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        if not ctx.tenant.prefix:
            return Answer(
                "Your token is not mapped to a customer, so I cannot retrieve alarms."
            )
        if not ctx.tenant.user_token:
            return Answer("A user token is required to read alarm data.")
        scoped = await self._scope_fn(ctx)
        device_ids = scoped.tb_device_ids
        requested, refuse = _requested_device(intent, device_ids)
        if refuse:
            return Answer("That device is not in your authorized scope.")
        if requested:
            device_ids = [requested]
        # "active alerts in the EAST zone" — narrow to the named area, intersected
        # with the scope so it can only ever subtract devices.
        area_ids, area_name = await area_device_filter(
            ctx.db,
            ctx.tenant.prefix,
            scoped.branch_node_ids,
            intent.raw_question,
            scoped.tb_device_ids,
        )
        if area_ids is not None:
            allowed = set(device_ids)
            device_ids = [device_id for device_id in area_ids if device_id in allowed]
            if not device_ids:
                return Answer(
                    f"No device under {area_name} is in your authorized scope.",
                    {"area": area_name, "alarms": []},
                )
        if not device_ids:
            return Answer("No branches are imported for your authorized scope yet.")

        client = self._client_factory(ctx.tb.settings, ctx.tenant.user_token)
        try:
            if requested:
                bodies = [await client.alarms(requested)]
            else:
                # Two reads, not one per device. ThingsBoard scopes /api/alarms to the
                # caller, so ~100 per-device calls collapse into these.
                #
                # ACTIVE is fetched separately and WHOLE: measured on production it is
                # 152 rows against 3,481 total, so it fits well inside the page cap.
                # Taking the open alarms out of a truncated recent-history window is how
                # "the oldest active alarm" silently becomes "the oldest one we happened
                # to read".
                bodies = [
                    await client.all_alarms(search_status="ACTIVE"),
                    await client.all_alarms(search_status="ANY"),
                ]
        except Exception as exc:
            logger.warning("alarm fetch failed", exc_info=True)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                # A dead token is not an outage; telling the user to retry wastes the
                # one failure that always needs them to sign in again.
                raise SessionExpired(f"thingsboard returned {status}") from exc
            return Answer(
                "I could not reach ThingsBoard alarm data just now. Please retry.",
                {"error": "thingsboard_unavailable"},
            )
        finally:
            await client.close()

        allowed = set(device_ids)
        branch_names = (
            dict(zip(scoped.tb_device_ids, scoped.branch_node_ids, strict=True))
            if len(scoped.tb_device_ids) == len(scoped.branch_node_ids)
            else {}
        )
        alarms: list[AlarmRecord] = []
        seen: set[str] = set()
        truncated = False
        for body in bodies:
            truncated = truncated or bool(isinstance(body, dict) and body.get("truncated"))
            rows = body.get("data", []) if isinstance(body, dict) else body
            if not isinstance(rows, list):
                continue
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                originator = raw.get("originator")
                device_id = (
                    originator.get("id") if isinstance(originator, dict) else originator
                ) or requested
                # The fleet endpoint returns everything ThingsBoard authorizes, which
                # can be wider than this caller's regional scope. Narrow, never widen.
                if device_id is None or str(device_id) not in allowed:
                    continue
                alarm = normalize_alarm(raw, str(device_id), branch_names.get(str(device_id)))
                if alarm is not None and alarm.alarm_id not in seen:
                    seen.add(alarm.alarm_id)
                    alarms.append(alarm)

        text, structured = format_alarm_answer(alarms, intent.raw_question)
        scoped_to = _scoped_to(intent, requested, area_name)
        if scoped_to:
            text = f"{scoped_to} — {text}"
            structured["area"] = scoped_to
        if truncated:
            text += (
                " ThingsBoard holds more alarm history than one read returns, so the "
                "resolved entries above cover only the most recent portion of it."
            )
            structured["truncated"] = True
        return Answer(
            text,
            structured,
            [{"type": "thingsboard-alarms", "resource": "scoped-devices"}],
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
        # Added with the key-doc slice; all answered by derived.py computations.
        "network_status",
        "sos_status",
        "connected_devices",
        "door_status",
        "cctv_storage",
        "cctv_camera_count",
        "cctv_camera_info",
        "cctv_sd_recording",
        "cctv_tamper_count",
        "bas_panel_info",
        "bas_power_status",
        "bas_zone_info",
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
    # Dotted paths are our addressing scheme, not ThingsBoard keys — ask for the
    # container ("gateway"), not "gateway.powerStatus", which matches nothing.
    wanted = request_keys(keys)
    series = await client.telemetry(device_id, keys=",".join(wanted) if wanted else None)
    if isinstance(series, dict):
        for key, points in series.items():
            if isinstance(points, list) and points and isinstance(points[0], dict):
                value = points[0].get("value")
                # A null telemetry reading must NOT erase a good attribute value.
                # Requesting keys explicitly makes ThingsBoard answer for keys that
                # have no timeseries at all — it returns {"gateway": [{"value": null}]}
                # — which used to overwrite the populated `gateway` attribute object
                # with None, so every subsystem read came back empty.
                if value is None and raw.get(str(key)) is not None:
                    continue
                raw[str(key)] = value
    return expand_containers(raw)


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
        # The extractor scrapes device_id from `(?:device|asset)\s+(\w+)`, so ordinary
        # question words arrive here as ids: "What NVR models are deployed?" produced
        # "'models' is not a valid device id." Echoing a word out of the user's own
        # sentence back at them as a rejected identifier is nonsense, and it happened
        # on 13 real questions. A non-UUID never came from the caller naming a device.
        if device_id and not _is_uuid(device_id):
            device_id = None
        if not device_id:
            return Answer(
                "That question needs a branch — name one (for example 'battery voltage "
                "of Liluah') and I will answer for it. For a fleet-wide view, ask about "
                "device health, CCTV recording, or alarms across all branches."
            )
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

        # A question about a PERIOD is answered from device_telemetry rather than a
        # live ThingsBoard fetch — ThingsBoard keeps no history for attributes, so our
        # hypertable is the only place the past exists.
        if intent.window is not None:
            historical = await _history_answer(intent, ctx, device_id)
            if historical is not None:
                return historical

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

        return _name_the_branch(_format_metric(intent, build_snapshot(raw), device_id), intent)


async def _history_answer(
    intent: ExtractedIntent, ctx: RequestContext, device_id: str
) -> Answer | None:
    """Answer from device_telemetry over the requested window.

    Returns None when the intent has no historical series worth summarising, so the
    caller falls through to the normal latest-value path rather than refusing.

    Scope is already enforced: MetricHandler.handle() verified device_id is in the
    caller's ThingsBoard-bounded scope before calling this.
    """
    window = intent.window
    if window is None:
        return None
    src = [{"type": "device_telemetry", "resource": f"device:{device_id}"}]

    numeric_key = history.NUMERIC_KEY_FOR_INTENT.get(intent.name)
    if numeric_key:
        summary = await history.numeric_summary(ctx.db, device_id, numeric_key, window.hours)
        if summary is None:
            return Answer(
                f"I have no recorded {numeric_key.replace('_', ' ')} for that device over "
                f"{window.label}.",
                {"key": numeric_key, "window_hours": window.hours, "samples": 0},
                src,
            )
        return Answer(
            f"{numeric_key.replace('_', ' ').title()} over {window.label}: "
            f"min {summary.minimum:g}, avg {summary.average:g}, max {summary.maximum:g} "
            f"({summary.samples} readings, latest {summary.latest:g}).",
            {
                "key": summary.key,
                "window_hours": window.hours,
                "samples": summary.samples,
                "min": summary.minimum,
                "avg": summary.average,
                "max": summary.maximum,
                "latest": summary.latest,
            },
            src,
        )

    status_key = history.STATUS_KEY_FOR_INTENT.get(intent.name)
    if status_key:
        status = await history.status_summary(ctx.db, device_id, status_key, window.hours)
        if status is None:
            return Answer(
                f"I have no recorded {status_key} history for that device over {window.label}.",
                {"key": status_key, "window_hours": window.hours, "samples": 0},
                src,
            )
        # distinct==1 means it never changed, which is the useful answer for a status.
        changed = (
            "it did not change"
            if status.distinct_values <= 1
            else f"it took {status.distinct_values} different values"
        )
        return Answer(
            f"Over {window.label} there were {status.samples} readings of {status_key} and "
            f"{changed}. Latest: {status.latest}.",
            {
                "key": status.key,
                "window_hours": window.hours,
                "samples": status.samples,
                "distinct_values": status.distinct_values,
                "latest": status.latest,
            },
            src,
        )
    return None


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
        # Live cameras outrank a stale status attribute. Production printed
        # "CCTV status is NOT_INSTALLED; 15/16 cameras online." — two halves of one
        # sentence contradicting each other, leaving the operator to guess which to
        # believe. The camera tally is direct evidence; cctv_sts is a cached field
        # that goes stale, so when they disagree the evidence wins and the stale
        # status is reported as what it is rather than as fact.
        online = c.online_camera_count or 0
        contradicts = online > 0 and c.state.value in ("NOT_INSTALLED", "UNKNOWN")
        if contradicts:
            text = (
                f"{online}/{c.camera_count} cameras are online, so CCTV is present and "
                f"reporting — though its status attribute still reads "
                f"{c.state.value}, which is stale."
            )
        else:
            text = (
                f"CCTV status is {c.state.value}; {online}/{c.camera_count} cameras online."
            )
        return Answer(
            text,
            {
                "cctv_state": c.state.value,
                "online": c.online_camera_count,
                "total": c.camera_count,
                "status_contradicts_cameras": contradicts,
            },
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

    # --- intents computed from the key doc rather than a snapshot field ---------
    raw = snap.raw_data

    if name == "network_status":
        net = derived.network_status(raw)
        return Answer(
            f"Network is {net['status']} (operator: {net['operator']}).", net, src
        )

    if name == "sos_status":
        sos = derived.sos_status(raw)
        if sos is None:
            return Answer("SOS status is not being reported for this device.", {}, src)
        return Answer(f"SOS status: {sos}.", {"sos_status": sos}, src)

    if name == "connected_devices":
        count = derived.connected_devices(raw)
        if count is None:
            return Answer("Connected-device count is not being reported.", {}, src)
        return Answer(f"{count} device(s) connected.", {"connected_devices": count}, src)

    if name == "door_status":
        doors = derived.door_status(raw)
        parts = [f"{k.replace('_', ' ')}: {v}" for k, v in doors.items() if v]
        if not parts:
            return Answer("No door status is being reported for this device.", doors, src)
        return Answer("Door status — " + ", ".join(parts) + ".", doors, src)

    if name == "cctv_storage":
        total = derived.hdd_total_capacity(raw)
        free = derived.hdd_free_space(raw)
        if total is None:
            return Answer("No CCTV storage capacity is being reported.", {}, src)
        text = f"CCTV storage: {total:g} total"
        if free is not None:
            text += f", {free:g} free"
        return Answer(
            text + f" across {len(derived.hdd_rows(raw))} disk(s).",
            {"total_capacity": total, "free_space": free},
            src,
        )

    if name == "cctv_camera_count":
        count = derived.camera_count(raw)
        return Answer(f"{count} camera(s) on this device.", {"camera_count": count}, src)

    if name == "cctv_camera_info":
        rows = derived.camera_rows(raw)
        return Answer(derived.summarize("Camera information", rows), {"cameras": rows}, src)

    if name == "cctv_sd_recording":
        rows = derived.sd_recording_rows(raw)
        return Answer(
            derived.summarize("SD recording information", rows), {"sd_recording": rows}, src
        )

    if name == "cctv_tamper_count":
        tamper = derived.camera_tamper_count(raw)
        disconnect = derived.camera_disconnect_count(raw)
        return Answer(
            f"Camera tamper count: {tamper if tamper is not None else 'not reported'}, "
            f"disconnect count: {disconnect if disconnect is not None else 'not reported'}.",
            {"tamper_count": tamper, "disconnect_count": disconnect},
            src,
        )

    if name == "bas_panel_info":
        panel = derived.bas_panel(raw)
        device = derived.bas_device(raw)
        parts = [f"{k.replace('_', ' ')}: {v}" for k, v in {**panel, **device}.items() if v]
        if not parts:
            return Answer("No BAS panel information is being reported.", {}, src)
        return Answer(
            "BAS panel — " + ", ".join(parts) + ".", {"panel": panel, "device": device}, src
        )

    if name == "bas_power_status":
        power = derived.bas_power(raw)
        parts = [f"{k.replace('_', ' ')}: {v}" for k, v in power.items() if v]
        if not parts:
            return Answer("No BAS power status is being reported.", power, src)
        return Answer("BAS power — " + ", ".join(parts) + ".", power, src)

    if name == "bas_zone_info":
        zones = derived.bas_zones(raw)
        return Answer(derived.summarize("BAS zone information", zones), {"zones": zones}, src)

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
