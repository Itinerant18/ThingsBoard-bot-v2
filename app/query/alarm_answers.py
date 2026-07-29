"""Normalize ThingsBoard alarms and format the alarm FAQ question families."""

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class AlarmRecord:
    alarm_id: str
    alarm_type: str
    severity: str
    branch: str
    zone: str | None
    region: str | None
    device_id: str
    created_at: datetime
    ended_at: datetime | None
    active: bool
    details: dict[str, Any]

    @property
    def duration(self) -> timedelta | None:
        if self.ended_at is None:
            return None
        return max(self.ended_at - self.created_at, timedelta())


def _timestamp(value: object) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    try:
        if text.replace(".", "", 1).isdigit():
            number = float(text)
            return datetime.fromtimestamp(number / (1000 if number > 10_000_000_000 else 1), UTC)
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _first(source: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        value: object | None = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _details_of(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten ThingsBoard's alarm details, lifting the `prev` observation.

    A cleared alarm carries its last observation under details.prev and leaves the
    outer object empty, so reading details directly loses branch/zone/region on
    exactly the alarms the history questions ask about.
    """
    details_raw = raw.get("details")
    details = dict(details_raw) if isinstance(details_raw, Mapping) else {}
    prev = details.pop("prev", None)
    if isinstance(prev, Mapping):
        # Outer keys win: they describe the alarm now, `prev` describes it when raised.
        return {**dict(prev), **details}
    return details


def normalize_alarm(
    raw: Mapping[str, Any], device_id: str, fallback_branch: str | None = None
) -> AlarmRecord | None:
    details = _details_of(raw)
    created = _timestamp(
        _first(raw, "startTs", "createdTime", "createdTs", "startTime", "time", "ts")
    )
    if created is None:
        return None
    status = str(raw.get("status") or "").upper()
    cleared_value = raw.get("cleared")
    cleared = (
        cleared_value is True
        or str(cleared_value).lower() == "true"
        or status.startswith("CLEARED")
    )
    # SECURITY OF THE ANSWER, not of access: ThingsBoard refreshes `endTs` every time
    # an OPEN alarm's condition is re-observed, so it is a last-seen stamp, not a
    # resolution time. Reading it as the end turns every ACTIVE_UNACK alarm into a
    # "resolved" one with a TAT of however long it has been broken — the exact
    # inverse of the answer. Only clearTs resolves; endTs is the fallback for older
    # payloads that recorded a clear without stamping clearTs.
    ended = _timestamp(_first(raw, "clearTs", "clearedTime")) if cleared else None
    if cleared and ended is None:
        ended = _timestamp(_first(raw, "endTs", "endTime"))
    active = not cleared
    branch = str(
        _first(raw, "originatorName", "originatorLabel")
        or _first(details, "branchName", "branch_name", "deviceName", "originatorName")
        or fallback_branch
        or device_id
    )
    alarm_type = str(
        _first(raw, "type", "name", "alarmType", "event_type")
        or _first(details, "alarmType", "logType", "logStatus", "type")
        or "Unknown alarm"
    )
    severity = str(_first(raw, "severity") or _first(details, "severity") or "UNKNOWN").upper()
    alarm_id_raw = raw.get("id")
    if isinstance(alarm_id_raw, Mapping):
        alarm_id_raw = alarm_id_raw.get("id")
    # zoName/nbgName are what the fleet actually emits ("ZO HOWRAH", "NBG EAST");
    # the generic zone/region spellings are kept for non-BOI payloads.
    zone = _first(details, "zoName", "zo_name", "zone", "zoneName", "zone_name")
    region = _first(details, "nbgName", "nbg_name", "region", "regionName", "fgmo")
    return AlarmRecord(
        alarm_id=str(alarm_id_raw or raw.get("alarmId") or f"{device_id}:{created.timestamp()}"),
        alarm_type=alarm_type,
        severity=severity,
        branch=branch,
        zone=str(zone) if zone is not None else None,
        region=str(region) if region is not None else None,
        device_id=device_id,
        created_at=created,
        ended_at=ended,
        active=active,
        details=details,
    )


def _duration_text(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3_600)
    minutes, _ = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts[:2])


# The operators reading these answers also read the ThingsBoard dashboard, which
# renders IST. Answering in UTC puts every timestamp 5h30m off what they see and
# makes a correct answer look wrong. India observes no DST, so a fixed offset is
# exact and needs no tzdata on the host.
# ponytail: constant, not config — one deployment, one timezone.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def _time_text(value: datetime) -> str:
    return value.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def _matches_type(alarm: AlarmRecord, question: str) -> bool:
    question = question.lower()
    kind = alarm.alarm_type.lower()
    if "camera disconnect" in question:
        return "camera" in kind and "disconnect" in kind
    if "camera tamper" in question:
        return "camera" in kind and "tamper" in kind
    if "integrated alarm" in question or "ias activation" in question:
        return ("integrated" in kind or "ias" in kind) and "activ" in kind
    if "fire alarm" in question:
        return "fire" in kind and "activ" in kind
    if "burglar alarm" in question or "intrusion alarm" in question:
        return ("burglar" in kind or "intrusion" in kind) and "activ" in kind
    # Generic fallbacks, after the specific pairs above. A question that says only
    # "cctv tamper" or "tamper alerts" still names a type; without these it matched
    # everything and answered with a dump of unrelated alarms.
    if "tamper" in question:
        return "tamper" in kind
    if "disconnect" in question:
        return "disconnect" in kind
    if "hdd" in question:
        return "hdd" in kind
    return True


def _alarm_line(alarm: AlarmRecord, now: datetime) -> str:
    line = f"{alarm.severity} {alarm.alarm_type} at {alarm.branch}, created {_time_text(alarm.created_at)}"
    if alarm.active:
        line += f", open for {_duration_text(now - alarm.created_at)} (no end time)"
    elif alarm.ended_at is not None:
        line += f", resolved {_time_text(alarm.ended_at)}"
        if alarm.duration is not None:
            line += f" (TAT {_duration_text(alarm.duration)})"
    return line


def _structured(alarms: list[AlarmRecord]) -> list[dict[str, Any]]:
    result = []
    for alarm in alarms:
        item = asdict(alarm)
        item["created_at"] = alarm.created_at.isoformat()
        item["ended_at"] = alarm.ended_at.isoformat() if alarm.ended_at else None
        item["duration_seconds"] = (
            int(alarm.duration.total_seconds()) if alarm.duration is not None else None
        )
        result.append(item)
    return result


# Word-bounded on purpose. Substring matching collides in both directions here:
# "unresolved" contains "resolved", and "currently resolved" contains "current".
# Either collision answers with the opposite set of alarms.
_OPEN_RE = re.compile(r"\b(?:unresolved|active|open|current(?:ly)?|right now|ongoing)\b")
_RESOLVED_RE = re.compile(r"\b(?:resolved|cleared)\b")
_UNRESOLVED_RE = re.compile(r"\bunresolved\b")


_HEALTH_QUESTION_RE = re.compile(
    r"\b(?:is|are)\b[^?]*\b(?:healthy|health|ok|okay|fine|working|operational|normal)\b"
)


def _asks_whether_healthy(text: str) -> bool:
    """True for "is X healthy?" — a question the alarm lister must never answer "Yes"
    to merely because it found alarms to list."""
    return bool(_HEALTH_QUESTION_RE.search(text))


def _asks_for_open(text: str) -> bool:
    """True when the question is about alarms that are still open.

    Precedence, and the reason it is not a plain word scan: "unresolved" settles the
    question outright. Otherwise naming a resolved state wins over a bare time adverb,
    so "how many alarms are currently resolved?" is a history question that happens to
    say "currently" — not a request for the open list.
    """
    if _UNRESOLVED_RE.search(text):
        return True
    if _RESOLVED_RE.search(text):
        return False
    return bool(_OPEN_RE.search(text))


def _asks_for_resolved(text: str) -> bool:
    return not _asks_for_open(text) and (
        bool(_RESOLVED_RE.search(text)) or "completed tat" in text
    )


def format_alarm_answer(
    alarms: list[AlarmRecord], question: str, now: datetime | None = None
) -> tuple[str, dict[str, Any]]:
    """Format active/history/TAT/severity alarm questions without LLM guessing."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    text = question.lower()
    active = [alarm for alarm in alarms if alarm.active]
    resolved = [alarm for alarm in alarms if not alarm.active and alarm.ended_at is not None]

    if "last hour" in text:
        selected = [alarm for alarm in alarms if alarm.created_at >= current - timedelta(hours=1)]
    elif "last 24" in text:
        selected = [alarm for alarm in alarms if alarm.created_at >= current - timedelta(hours=24)]
    elif _asks_for_open(text):
        selected = active
    elif _asks_for_resolved(text):
        selected = resolved
    else:
        selected = alarms
    selected = [alarm for alarm in selected if _matches_type(alarm, text)]

    if "severity breakdown" in text:
        counts = Counter(alarm.severity for alarm in active)
        answer = (
            "Active alarm severity breakdown: "
            + ", ".join(f"{severity.title()} {count}" for severity, count in counts.items())
            + "."
            if counts
            else "No active alarms are currently visible in your authorized scope."
        )
        return answer, {"severity": dict(counts), "alarms": _structured(active)}

    if "which zone" in text and ("unresolved" in text or "active" in text):
        locations = sorted(
            {
                f"{alarm.zone} ({alarm.region})" if alarm.region else str(alarm.zone)
                for alarm in active
                if alarm.zone
            }
        )
        answer = (
            "Zones with active unresolved alarms: " + ", ".join(locations) + "."
            if locations
            else "The active alarm records do not include zone information."
        )
        return answer, {"zones": locations, "alarms": _structured(active)}

    if "branch" in text and "attention" in text:
        if not active:
            return "No branch currently needs alarm attention.", {"alarms": []}
        weights = {"CRITICAL": 5, "MAJOR": 4, "MINOR": 3, "WARNING": 2}
        by_branch: dict[str, list[AlarmRecord]] = {}
        for alarm in active:
            by_branch.setdefault(alarm.branch, []).append(alarm)
        ranked = sorted(
            by_branch.items(),
            key=lambda item: (
                max(weights.get(alarm.severity, 1) for alarm in item[1]),
                len(item[1]),
            ),
            reverse=True,
        )
        lines = [
            f"{branch}: {len(items)} active alarm(s), including "
            + ", ".join(f"{alarm.severity} {alarm.alarm_type}" for alarm in items[:3])
            for branch, items in ranked
        ]
        return (
            "Branches needing immediate attention, highest priority first: "
            + "; ".join(lines)
            + ".",
            {"branches": [branch for branch, _ in ranked], "alarms": _structured(active)},
        )

    if "active critical" in text:
        critical = [alarm for alarm in active if alarm.severity == "CRITICAL"]
        if critical:
            answer = f"Yes. {len(critical)} active Critical alarm(s) are visible: " + "; ".join(
                _alarm_line(alarm, current) for alarm in critical[:5]
            ) + "."
        else:
            highest = next(
                (level for level in ("MAJOR", "MINOR", "WARNING", "INDETERMINATE") if any(a.severity == level for a in active)),
                None,
            )
            answer = "No active Critical alarms are visible."
            if highest:
                answer += f" The highest active severity is {highest.title()}."
        return answer, {"alarms": _structured(critical)}

    if "oldest" in text or "open the longest" in text or "longest current tat" in text:
        if not active:
            return "No active alarms are currently visible in your authorized scope.", {"alarms": []}
        oldest = min(active, key=lambda alarm: alarm.created_at)
        return (
            f"The longest-open alarm is {_alarm_line(oldest, current)}.",
            {"alarms": _structured([oldest])},
        )

    if "latest resolved" in text:
        if not resolved:
            return "No resolved alarms are visible in the available alarm history.", {"alarms": []}
        latest = max(resolved, key=lambda alarm: alarm.ended_at or alarm.created_at)
        return f"The latest resolved alarm was {_alarm_line(latest, current)}.", {"alarms": _structured([latest])}

    if "longest resolved" in text or "longest completed" in text:
        completed = [alarm for alarm in resolved if alarm.duration is not None]
        if not completed:
            return "No completed alarm TAT is available in the alarm history.", {"alarms": []}
        longest = max(completed, key=lambda alarm: alarm.duration or timedelta())
        return f"The longest completed alarm was {_alarm_line(longest, current)}.", {"alarms": _structured([longest])}

    if "current tat" in text or "live tat" in text:
        if not active:
            return "No active alarm has a live TAT right now.", {"alarms": []}
        lines = [
            f"{alarm.alarm_type} at {alarm.branch}: {_duration_text(current - alarm.created_at)}"
            for alarm in sorted(active, key=lambda alarm: alarm.created_at)
        ]
        return (
            "Active alarms have no final TAT until they end. Current live durations: "
            + "; ".join(lines[:10])
            + ".",
            {"alarms": _structured(active)},
        )

    if "which branches" in text and ("unresolved" in text or "active alarm" in text):
        branches = sorted({alarm.branch for alarm in active})
        answer = (
            "Branches with active unresolved alarms: " + ", ".join(branches) + "."
            if branches
            else "No branch currently has an active unresolved alarm in your authorized scope."
        )
        return answer, {"branches": branches, "alarms": _structured(active)}

    if "alarm types" in text or "types of alarms" in text:
        types = sorted({alarm.alarm_type for alarm in selected})
        answer = "Visible alarm types: " + ", ".join(types) + "." if types else "No alarm types are visible."
        return answer, {"types": types, "alarms": _structured(selected)}

    if not selected:
        if "last hour" in text:
            answer = "No alarms were triggered in the last hour."
        elif "last 24" in text:
            answer = "No alarms were triggered in the last 24 hours."
        elif _asks_for_open(text):
            answer = "No matching active alarm is currently visible in your authorized scope."
        else:
            answer = "No matching alarm is visible in the available alarm history."
        matching_resolved = [alarm for alarm in resolved if _matches_type(alarm, text)]
        if matching_resolved and _asks_for_open(text):
            latest = max(matching_resolved, key=lambda alarm: alarm.ended_at or alarm.created_at)
            answer += f" The latest matching resolved alarm was {_alarm_line(latest, current)}."
        return answer, {"alarms": _structured(matching_resolved[:1])}

    # SAFETY: "Yes" here affirms that matching alarms EXIST. On a question phrased
    # "Is the Burglar Alarm System healthy?" that same word reads as "yes, healthy"
    # — while the sentence goes on to list an active intrusion alarm. Answering yes
    # to a health question about a bank's intrusion system, on evidence that says
    # the opposite, is the worst failure this module can produce.
    if _asks_whether_healthy(text):
        count = len(selected)
        return (
            f"No — {count} alarm(s) match, so this is not clear: "
            + "; ".join(_alarm_line(alarm, current) for alarm in selected[:10])
            + ".",
            {"alarms": _structured(selected), "healthy": False},
        )
    prefix = "Yes. " if text.startswith(("is ", "are ", "do ")) else ""
    return (
        prefix + "; ".join(_alarm_line(alarm, current) for alarm in selected[:10]) + ".",
        {"alarms": _structured(selected)},
    )
