import re

from app.query.contracts import ExtractedIntent


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

    async def extract(self, question: str) -> ExtractedIntent:
        text = question.lower()

        def has(*words: str) -> bool:
            return any(w in text for w in words)

        cctv = "cctv" in text or "camera" in text
        # "how many …" wants a count (global_overview); "list/show/which …" wants the
        # names (device_inventory). Count is checked first so "how many branches do you
        # list" is still a count.
        if has("how many", "count of", "total number") and not cctv:
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
