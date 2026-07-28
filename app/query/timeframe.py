"""Time windows parsed from a question, for historical answers.

Phase 2 of conversational memory. Everything the bot answered until now was "the
latest value"; device_telemetry has held history since the persistence slice but
nothing read it. A window turns "battery voltage of Liluah LAST WEEK" into a range
query over that table instead of a live ThingsBoard call for the current value.

Deliberately a small vocabulary of explicit phrases rather than a general date
parser: a wrong window silently answers about the wrong period, which is harder to
notice than a refusal. Anything unrecognised yields None and the caller keeps its
existing latest-value behaviour.
"""

import re
from dataclasses import dataclass

# Bounded so a question cannot ask for a scan of the whole hypertable.
MAX_WINDOW_HOURS = 365 * 24


@dataclass(frozen=True)
class TimeWindow:
    hours: int
    label: str  # human phrasing, echoed back in the answer


_PHRASES: tuple[tuple[str, int, str], ...] = (
    ("last 24 hours", 24, "the last 24 hours"),
    ("past 24 hours", 24, "the last 24 hours"),
    ("last day", 24, "the last day"),
    ("yesterday", 24, "yesterday"),
    ("today", 24, "today"),
    ("last week", 24 * 7, "the last week"),
    ("past week", 24 * 7, "the last week"),
    ("last 7 days", 24 * 7, "the last 7 days"),
    ("last fortnight", 24 * 14, "the last fortnight"),
    ("last month", 24 * 30, "the last month"),
    ("past month", 24 * 30, "the last month"),
    ("last 30 days", 24 * 30, "the last 30 days"),
    ("last quarter", 24 * 90, "the last quarter"),
    ("last 90 days", 24 * 90, "the last 90 days"),
    ("last year", 24 * 365, "the last year"),
    ("past year", 24 * 365, "the last year"),
)

# "last 3 days", "past 12 hours", "last 2 weeks", "last 6 months"
_NUMERIC = re.compile(
    r"\b(?:last|past|previous)\s+(\d{1,3})\s*(hour|hr|day|week|month)s?\b", re.IGNORECASE
)
_UNIT_HOURS = {"hour": 1, "hr": 1, "day": 24, "week": 24 * 7, "month": 24 * 30}


def parse_window(question: str) -> TimeWindow | None:
    """A time window, or None when the question does not ask for one."""
    text = question.lower()

    match = _NUMERIC.search(text)
    if match:
        count = int(match.group(1))
        unit = match.group(2).lower()
        hours = min(count * _UNIT_HOURS[unit], MAX_WINDOW_HOURS)
        if hours >= 1:
            plural = "" if count == 1 else "s"
            return TimeWindow(hours=hours, label=f"the last {count} {unit}{plural}")

    # Longest phrase first so "last 24 hours" is not shadowed by "last day".
    for phrase, hours, label in sorted(_PHRASES, key=lambda p: -len(p[0])):
        if phrase in text:
            return TimeWindow(hours=hours, label=label)
    return None


def asks_for_history(question: str) -> bool:
    """True when the question is about the past rather than the current value.

    A window phrase is the strong signal; these verbs catch "how has the battery
    voltage TRENDED" where no explicit period is given.
    """
    text = question.lower()
    if parse_window(text) is not None:
        return True
    return any(
        word in text
        for word in ("trend", "history", "historical", "over time", "average", "minimum", "maximum")
    )
