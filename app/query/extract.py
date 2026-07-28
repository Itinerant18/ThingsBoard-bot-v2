import re
from typing import TYPE_CHECKING

from app.query.contracts import ExtractedIntent
from app.query.timeframe import TimeWindow, parse_window

if TYPE_CHECKING:
    from app.query.memory import ChatContext

# Words that carry an intent of their own. A question containing none of these is
# almost certainly a follow-up leaning on the previous turn.
_INTENT_WORDS = (
    "gateway", "battery", "volt", "current", "power", "cctv", "camera", "hdd",
    "network", "operator", "sos", "door", "alarm", "alert", "subsystem", "ias",
    "fas", "bas", "time lock", "access control", "device", "branch", "inventory",
    "status", "health", "hardware", "firmware", "recording", "storage", "zone",
    "panel", "connected", "tamper", "disconnect", "fault", "tls", "acs",
)

# Openers that explicitly reference the previous turn.
_FOLLOW_UP_MARKERS = (
    "what about", "how about", "and ", "also", "same for", "that one", "it",
    "why", "when", "compare", "instead", "too", "as well", "last week",
    "yesterday", "last month", "previous",
)


def _is_fragment(text: str) -> bool:
    """True when a question cannot stand on its own and needs the previous turn.

    Deliberately conservative: it requires the question to name NO intent of its own.
    A false positive would answer the wrong question, so anything self-contained
    ("cctv status of Liluah") is classified normally even if it also says "and".
    """
    stripped = text.strip()
    if not stripped:
        return False
    if any(word in stripped for word in _INTENT_WORDS):
        return False
    return any(marker in stripped for marker in _FOLLOW_UP_MARKERS) or len(stripped.split()) <= 4


# "current" is overwhelmingly the adjective ("current status", "currently active"),
# not the amperage reading. Matching the bare word routed a fifth of every question
# the operators ask — compliance scores, user lists, SLA — into the system_current
# metric handler, which answered each one with an ammeter reading. A question that
# means amps says so.
_AMPERAGE_RE = re.compile(
    r"\b(?:system current|current draw|load current|battery current|current reading"
    r"|amperage|amps?|milliamps?|\d+\s*a\b)\b"
)


def _detect_subsystem(text: str) -> str | None:
    if "gateway" in text:
        return "gateway"
    if "cctv" in text or "camera" in text:
        return "cctv"
    if "intrusion" in text or re.search(r"\bias\b", text):
        return "ias"
    if "fire" in text or re.search(r"\bfas\b", text):
        return "fas"
    if re.search(r"\bbas\b", text):
        return "bas"
    if "time lock" in text or re.search(r"\btls\b", text):
        return "timeLock"
    if "access control" in text or re.search(r"\bacs\b", text):
        return "accessControl"
    return None


# Structural questions about the org tree. Deliberately narrow: anything about the
# STATE of devices in an area belongs to fleet_health or alarm_detail, not here.
_HIERARCHY_RE = re.compile(
    r"\borganization hierarchy\b|\borganisation hierarchy\b|\bhierarchy structure\b"
    r"|\bhierarchy level|\b(?:zones?|branch(?:es)?|regions?) (?:are |currently )?under\b"
    r"|\b(?:under|within) the \w+(?: \w+)? (?:zone|region|nbg|fgmo)\b"
    r"|\bis there a[n]? .* (?:branch|zone|region)\b"
    r"|\bmost branches\b|\bhow many (?:zones?|regions?|branches)\b"
)


def _is_hierarchy_question(text: str) -> bool:
    if not _HIERARCHY_RE.search(text):
        return False
    # "health status of all devices in the EAST zone" names an area but asks about
    # state; the fleet handlers own that. Structure questions ask WHICH or HOW MANY.
    return not re.search(
        r"\bhealth|\bhealthy|\bfaulty|\boffline|\balarm|\balert|\buser|\bstatus\b", text
    )


_AUDIT_RE = re.compile(
    r"\baudit\b|\baudit log|\bactivity log|\bwhat changed\b|\bwho changed\b"
    r"|\bconfiguration change|\bconfig change|\bsystem change|\bchanges were made"
    r"|\bwho did what\b|\bfailed (?:login|action)"
    # "what actions were performed by X" is a question about the trail, not the
    # directory — it says "user" but wants activity.
    r"|\bactions? (?:were |was )?performed\b|\bperformed by\b"
)
_USER_RE = re.compile(r"\buser|\blogin|\blogged in|\baccount(s)?\b|\brole(s)?\b|\bpermission")
# "device user" is not a person, and a role question about MODULES is a config question.
_NOT_USER_RE = re.compile(r"\bdevice user|\buser device|\bend[- ]user device")


def _is_user_question(text: str) -> bool:
    if _NOT_USER_RE.search(text) or not _USER_RE.search(text):
        return False
    # "roles"/"permissions" alone are ambiguous; require a person-shaped subject.
    if re.search(r"\buser|\blogin|\blogged in|\baccount", text):
        return True
    return bool(re.search(r"\brole|\bpermission|\badmin access", text))


# Fleet-shaped CCTV questions. The cctv_* intents all resolve one branch's NVR, so a
# question spanning branches routed there answered for a single device or refused for
# want of a device_id.
_FLEET_SCOPE = (
    "across all branch", "all branches", "across branches", "every branch",
    "which branch", "which branches", "fleet", "across all device", "each cctv",
    "each branch", "per branch",
)
_RECORDING_WORDS = (
    "recording", "record", "retention", "footage", "storage consumption",
)


def _is_fleet_cctv_question(text: str, cctv: bool) -> bool:
    if not cctv and not any(word in text for word in _RECORDING_WORDS):
        return False
    fleet_scoped = any(phrase in text for phrase in _FLEET_SCOPE)
    if fleet_scoped and (cctv or "recording" in text or "camera" in text):
        return True
    # "Are there any recording failures right now?" names no scope but is fleet-wide
    # by nature: there is no single branch it could mean.
    return any(
        phrase in text
        for phrase in (
            "recording failure",
            "recording gap",
            "recording health",
            "recording status",
            "not recording",
            "no recording",
            "recording compliance",
            "storage consumption",
            "cctv inventory",
            "inventory status of cctv",
            "camera models",
            "cctv camera models",
            "cameras deployed",
            "cameras are deployed",
        )
    )


def _is_fleet_health_question(text: str) -> bool:
    """Question families backed by the deployed-module health aggregation."""
    phrases = (
        "device category",
        "device categories",
        "health distribution",
        "health percentage",
        "health score",
        "healthy devices",
        "faulty devices",
        "offline devices",
        "monitored systems",
        "system health",
        "system healthy",
        "status of our",
        "needs attention",
        "need attention",
        "all devices healthy",
        # Area-scoped health: the handler narrows to the named area, so these belong
        # to fleet_health rather than falling through to the overview default.
        "health status of all devices",
        "device health status",
        "devices are healthy",
        "worst overall device health",
        "device count per zone",
        "zones currently have",
    )
    if any(phrase in text for phrase in phrases):
        return True
    named_category = _detect_subsystem(text) is not None
    if named_category and "device" in text and any(
        state in text for state in ("healthy", "faulty", "offline", "deployed")
    ):
        return True
    return named_category and any(
        phrase in text
        for phrase in (
            "devices deployed",
            "system healthy",
            "devices are healthy",
            "devices are offline",
            "devices are faulty",
            "status of all",
        )
    )


class KeywordIntentExtractor:
    """Deterministic keyword classifier; safe without an LLM key, and the fail-closed
    fallback for LlmIntentExtractor. ponytail: a stand-in for the full Java
    QueryIntentResolver (60 intents, fuzzy branch match, glossary) — port that when
    branch snapshots + alias index land."""

    async def extract(
        self, question: str, context: "ChatContext | None" = None
    ) -> ExtractedIntent:
        text = question.lower()

        # A fragment carries no intent words of its own ("and last week?", "why?",
        # "what about Howrah"). Without the previous intent it falls through to the
        # global_overview default at the bottom of this chain and answers a question
        # nobody asked — the single most visible way the bot "forgets".
        # A window named in THIS question always wins; otherwise a fragment keeps
        # the period from the previous turn ("...last week" then "and Howrah?").
        window = parse_window(question)
        inherited = (
            TimeWindow(context.window_hours, context.window_label or "that period")
            if window is None
            and context is not None
            and context.window_hours
            and _is_fragment(text)
            else None
        )

        if context is not None and context.intent and _is_fragment(text):
            return ExtractedIntent(
                name=context.intent,
                device_id=None,  # the orchestrator supplies the remembered device
                subsystem=_detect_subsystem(text) or None,
                raw_question=question,
                window=window or inherited,
            )

        def has(*words: str) -> bool:
            return any(w in text for w in words)

        cctv = "cctv" in text or "camera" in text
        bas = bool(re.search(r"\bbas\b", text))

        # SPECIFIC BEFORE GENERIC. This is an ordered chain, so every branch below is
        # unreachable once a broader one above matches. Three collisions found by test:
        #   "bas power status"       -> would hit the generic "power" branch
        #   "how many connected devices" -> would hit the generic "how many" count
        #   "bas panel state"        -> would hit the generic subsystem branch
        if (
            has("alarm", "alert")
            or ("branch" in text and "attention" in text)
            or ("issue" in text and cctv)
        ):
            intent = "alarm_detail"
        elif _is_hierarchy_question(text):
            intent = "hierarchy_info"
        elif _AUDIT_RE.search(text):
            # Before the user branch: "who logged in recently" is a question about the
            # audit TRAIL, while "which users have never logged in" is about the
            # directory. Audit wins only when the question names audit-shaped things.
            intent = "audit_log"
        elif _is_user_question(text):
            intent = "user_directory"
        elif _is_fleet_cctv_question(text, cctv):
            intent = "cctv_fleet"
        elif _is_fleet_health_question(text):
            intent = "fleet_health"
        elif bas and has("zone"):
            intent = "bas_zone_info"
        elif bas and has("panel", "heartbeat", "mode"):
            intent = "bas_panel_info"
        elif bas and has("power", "voltage", "current"):
            intent = "bas_power_status"
        elif has("sos"):
            intent = "sos_status"
        elif has("connected device", "devices connected", "no of connected"):
            intent = "connected_devices"
        elif has("network", "operator", "sim card", "signal strength"):
            intent = "network_status"
        elif has("door"):
            intent = "door_status"
        elif cctv and has("storage", "capacity", "free space", "disk space"):
            intent = "cctv_storage"
        elif cctv and has("sd record", "sd card"):
            intent = "cctv_sd_recording"
        elif cctv and has("tamper", "disconnect"):
            intent = "cctv_tamper_count"
        elif cctv and has("how many", "total number", "count of", "number of"):
            intent = "cctv_camera_count"
        elif cctv and has("camera info", "camera detail", "camera list", "channel"):
            intent = "cctv_camera_info"
        # "how many …" wants a count (global_overview); "list/show/which …" wants the
        # names (device_inventory). Count is checked first so "how many branches do you
        # list" is still a count.
        elif has("how many", "count of", "total number") and not cctv:
            intent = "global_overview"
        elif has(
            "inventory",
            "list device",
            "list branch",
            "list my",
            "show me the branch",
            "which branch",
            "what branch",
            "name the branch",
            "all branches",
            "active region",
            "region is currently active",
            "visible on the map",
            "branches on the map",
        ):
            intent = "device_inventory"
        elif "gateway" in text:
            intent = "gateway_status"
        elif "battery" in text and "volt" in text:
            intent = "battery_voltage"
        elif "battery" in text and "low" in text:
            intent = "battery_low_status"
        elif "battery" in text:
            intent = "battery_health"
        elif re.search(r"\bac\b", text) and "volt" in text:
            intent = "ac_voltage"
        elif _AMPERAGE_RE.search(text):
            intent = "system_current"
        elif "power" in text:
            intent = "power_status"
        elif cctv and "record" in text:
            intent = "cctv_recording_info"
        elif cctv and any(
            w in text
            for w in ("model", "nvr", "dvr", "vendor", "brand", "make", "inventory", "spec", "resolution")
        ):
            intent = "cctv_device_info"
        elif cctv and "hdd" in text and "error" in text:
            intent = "cctv_hdd_error_status"
        elif cctv and "hdd" in text:
            intent = "cctv_hdd_info"
        elif cctv:
            intent = "cctv_status"
        elif has("cpu", "memory", "disk", "temperature", "hardware", "firmware"):
            intent = "device_hardware"
        elif has("subsystem", "ias", "fas", "bas", "access control", "time lock", "fault"):
            intent = "subsystem_status"
        else:
            intent = "global_overview"

        device = re.search(r"(?:device|asset)\s+([\w-]+)", question, re.IGNORECASE)
        return ExtractedIntent(
            name=intent,
            device_id=device.group(1) if device else None,
            subsystem=_detect_subsystem(text),
            raw_question=question,
            window=window,
        )
