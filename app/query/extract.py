import re
from typing import TYPE_CHECKING

from app.query.contracts import ExtractedIntent

if TYPE_CHECKING:
    from app.query.memory import ChatContext

# Words that carry an intent of their own. A question containing none of these is
# almost certainly a follow-up leaning on the previous turn.
_INTENT_WORDS = (
    "gateway", "battery", "volt", "current", "power", "cctv", "camera", "hdd",
    "network", "operator", "sos", "door", "alarm", "alert", "subsystem", "ias",
    "fas", "bas", "time lock", "access control", "device", "branch", "inventory",
    "status", "health", "hardware", "firmware", "recording", "storage", "zone",
    "panel", "connected", "tamper", "disconnect", "fault",
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


def _detect_subsystem(text: str) -> str | None:
    if "cctv" in text or "camera" in text:
        return "cctv"
    if "intrusion" in text or re.search(r"\bias\b", text):
        return "ias"
    if "fire" in text or re.search(r"\bfas\b", text):
        return "fas"
    if re.search(r"\bbas\b", text):
        return "bas"
    if "time lock" in text:
        return "timeLock"
    if "access control" in text:
        return "accessControl"
    return None


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
        if context is not None and context.intent and _is_fragment(text):
            return ExtractedIntent(
                name=context.intent,
                device_id=None,  # the orchestrator supplies the remembered device
                subsystem=_detect_subsystem(text) or None,
                raw_question=question,
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
        if bas and has("zone"):
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
        elif has("inventory", "list device", "list branch", "list my", "show me the branch",
                 "which branch", "what branch", "name the branch", "all branches"):
            intent = "device_inventory"
        elif has("alarm", "alert"):
            intent = "alarm_detail"
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
        elif "current" in text:
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
        )
