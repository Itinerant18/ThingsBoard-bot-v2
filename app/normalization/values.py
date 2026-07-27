"""Value normalization — direct port of Java ValueNormalizer + NormalizedState.

Contract: docs/thingsboard-key-map.md §11. Keyword lists and corrupt-value
handling must match the Java exactly; the LLM's deterministic answers depend on
identical mapping.
"""

from enum import Enum


class NormalizedState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    FAULT = "FAULT"
    NOT_INSTALLED = "NOT_INSTALLED"
    UNKNOWN = "UNKNOWN"


def _is_null_or_corrupt(text: object) -> bool:
    if text is None:
        return True
    value = str(text).strip().lower()
    return (
        value == ""
        or value == "null"
        or value.startswith("null")
        or value == "not_found"
        or value == "not found"
    )


def to_state(raw: object) -> NormalizedState:
    if _is_null_or_corrupt(raw):
        return NormalizedState.UNKNOWN
    value = str(raw).strip().lower()
    if value in {"online", "on", "healthy", "active", "true", "1", "yes", "clear", "normal"}:
        return NormalizedState.ONLINE
    if value in {"offline", "off", "inactive", "false", "0", "disconnected"}:
        return NormalizedState.OFFLINE
    if value in {"fault", "alarm", "error", "tamper", "triggered", "critical"}:
        return NormalizedState.FAULT
    if value in {"n/a", "na", "not installed", "-"}:
        return NormalizedState.NOT_INSTALLED
    return NormalizedState.UNKNOWN


def to_bool(raw: object) -> bool | None:
    if _is_null_or_corrupt(raw):
        return None
    value = str(raw).strip().lower()
    if value in {"true", "1", "yes", "on", "healthy", "online"}:
        return True
    if value in {"false", "0", "no", "off", "offline", "fault", "inactive"}:
        return False
    return None


def to_double(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if _is_null_or_corrupt(text) or text.lower() in {"n/a", "na"} or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(raw: object, fallback: int) -> int:
    # ponytail: int(text) not int(float(text)) — Java parseInt rejects "3.0" -> fallback.
    if raw is None:
        return fallback
    text = str(raw).strip()
    if _is_null_or_corrupt(text) or text.lower() in {"n/a", "na"} or text == "-":
        return fallback
    try:
        return int(text)
    except ValueError:
        return fallback
