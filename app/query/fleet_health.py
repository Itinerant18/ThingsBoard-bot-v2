"""Fleet-wide device-category health aggregation and answer formatting.

The FAQ describes dashboard-level *module* counts: one branch may contribute a
Gateway, CCTV, IAS, BAS, FAS, TLS, and ACS device.  Counting hierarchy leaves
therefore answers a different question.  This module expands each scoped branch
snapshot into its deployed modules, then aggregates their normalized states.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.normalization.snapshot import BranchSnapshot
from app.normalization.values import NormalizedState


@dataclass
class CategoryHealth:
    label: str
    healthy: int = 0
    faulty: int = 0
    offline: int = 0
    unknown: int = 0

    @property
    def total(self) -> int:
        return self.healthy + self.faulty + self.offline + self.unknown

    @property
    def health_percentage(self) -> float | None:
        return None if self.total == 0 else self.healthy * 100 / self.total


@dataclass
class FleetHealthSummary:
    categories: dict[str, CategoryHealth]
    branches: dict[str, dict[str, str]]
    open_alerts: int

    @property
    def total(self) -> int:
        return sum(item.total for item in self.categories.values())

    @property
    def healthy(self) -> int:
        return sum(item.healthy for item in self.categories.values())

    @property
    def faulty(self) -> int:
        return sum(item.faulty for item in self.categories.values())

    @property
    def offline(self) -> int:
        return sum(item.offline for item in self.categories.values())

    @property
    def unknown(self) -> int:
        return sum(item.unknown for item in self.categories.values())

    @property
    def health_percentage(self) -> float | None:
        return None if self.total == 0 else self.healthy * 100 / self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "healthy": self.healthy,
            "faulty": self.faulty,
            "offline": self.offline,
            "unknown": self.unknown,
            "health_percentage": self.health_percentage,
            "open_alerts": self.open_alerts,
            "categories": {
                key: asdict(value)
                | {"total": value.total, "health_percentage": value.health_percentage}
                for key, value in self.categories.items()
            },
            "branches": self.branches,
        }


_LABELS = {
    "gateway": "Gateway",
    "cctv": "CCTV",
    "ias": "IAS",
    "bas": "BAS",
    "fas": "FAS",
    "timeLock": "TLS",
    "accessControl": "ACS",
}


def _add_state(category: CategoryHealth, state: NormalizedState) -> None:
    if state == NormalizedState.ONLINE:
        category.healthy += 1
    elif state == NormalizedState.FAULT:
        category.faulty += 1
    elif state == NormalizedState.OFFLINE:
        category.offline += 1
    else:
        category.unknown += 1


def aggregate_fleet_health(
    snapshots: dict[str, BranchSnapshot], expected_gateway_ids: list[str]
) -> FleetHealthSummary:
    """Aggregate only deployed modules while retaining missing Gateway snapshots.

    Every hierarchy leaf represents a branch Gateway, so a scoped branch without a
    recent snapshot is an UNKNOWN Gateway.  Other modules are counted only when the
    authoritative subsystem state says they are installed; guessing their deployment
    from a missing snapshot would inflate fleet totals.
    """
    categories = {key: CategoryHealth(label) for key, label in _LABELS.items()}
    branches: dict[str, dict[str, str]] = {}
    open_alerts = 0

    for device_id in expected_gateway_ids:
        snap = snapshots.get(device_id)
        if snap is None:
            categories["gateway"].unknown += 1
            branches[device_id] = {"Gateway": NormalizedState.UNKNOWN.value}
            continue

        branch_name = snap.identity.branch_name or snap.identity.technical_id or device_id
        branch_states: dict[str, str] = {}
        _add_state(categories["gateway"], snap.gateway.state)
        branch_states["Gateway"] = snap.gateway.state.value

        subsystems = {
            "cctv": snap.subsystems.cctv,
            "ias": snap.subsystems.ias,
            "bas": snap.subsystems.bas,
            "fas": snap.subsystems.fas,
            "timeLock": snap.subsystems.time_lock,
            "accessControl": snap.subsystems.access_control,
        }
        for key, subsystem in subsystems.items():
            if not subsystem.installed:
                continue
            _add_state(categories[key], subsystem.state)
            branch_states[_LABELS[key]] = subsystem.state.value

        open_alerts += max(snap.alerts.alarm_count, 0)
        branches[branch_name] = branch_states

    return FleetHealthSummary(categories, branches, open_alerts)


def normalize_category(value: str | None, question: str = "") -> str | None:
    text = f"{value or ''} {question}".lower()
    if "gateway" in text:
        return "gateway"
    if "cctv" in text or "camera" in text:
        return "cctv"
    if "integrated alarm" in text or re.search(r"\bias\b", text):
        return "ias"
    if "burglar" in text or "intrusion" in text or re.search(r"\bbas\b", text):
        return "bas"
    if "fire alarm" in text or re.search(r"\bfas\b", text):
        return "fas"
    if "time lock" in text or re.search(r"\btls\b", text):
        return "timeLock"
    if "access control" in text or re.search(r"\bacs\b", text):
        return "accessControl"
    return None


def _count_phrase(item: CategoryHealth) -> str:
    text = (
        f"{item.healthy} healthy, {item.faulty} faulty, "
        f"{item.offline} offline"
    )
    if item.unknown:
        text += f", {item.unknown} unknown"
    return text


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _verb(count: int) -> str:
    """Agreement for the counts these sentences are built from; "1 are faulty" is
    the kind of wrong that makes a correct number look untrustworthy."""
    return "is" if count == 1 else "are"


def format_fleet_health(
    summary: FleetHealthSummary, question: str, subsystem: str | None = None
) -> str:
    """Answer the FAQ's health question families from one canonical summary."""
    text = question.lower()
    category_key = normalize_category(subsystem, question)

    if category_key is not None:
        item = summary.categories[category_key]
        if "deployed" in text or (category_key == "accessControl" and item.total == 0):
            if item.total == 0:
                return f"No. The current scoped data shows 0 {item.label} devices deployed."
            return f"Yes. {item.total} {item.label} {_plural(item.total, 'device')} are deployed."
        if item.total == 0:
            return f"No {item.label} devices are deployed in the current scope."
        pct = item.health_percentage or 0.0
        if "how many" in text and "offline" in text:
            return (
                f"{item.offline} {item.label} {_plural(item.offline, 'device')} "
                f"{_verb(item.offline)} offline; "
                f"{item.healthy} of {item.total} are healthy."
            )
        if "how many" in text and "fault" in text:
            return f"{item.faulty} of {item.total} {item.label} devices are faulty."
        qualifier = (
            "healthy"
            if item.healthy == item.total
            else "mostly healthy"
            if pct >= 50
            else "unhealthy"
        )
        return (
            f"{item.label} is {qualifier}: {item.healthy} of {item.total} devices are healthy "
            f"({pct:.1f}%), with {item.faulty} faulty and {item.offline} offline"
            + (f"; {item.unknown} have unknown status" if item.unknown else "")
            + "."
        )

    deployed = [item for item in summary.categories.values() if item.total > 0]
    if "most faulty" in text:
        top = max(deployed, key=lambda item: item.faulty, default=None)
        if top is None or top.faulty == 0:
            return "No deployed device category currently has a faulty device."
        return f"{top.label} has the most faulty devices, with {top.faulty}."
    if "most offline" in text:
        ranked = sorted(deployed, key=lambda item: item.offline, reverse=True)
        if not ranked or ranked[0].offline == 0:
            return "No deployed device category currently has an offline device."
        leaders = [item for item in ranked if item.offline > 0]
        return "Offline devices by category: " + ", ".join(
            f"{item.label} {item.offline}" for item in leaders
        ) + "."
    if "lowest health" in text:
        lowest = min(deployed, key=lambda item: item.health_percentage or 0.0, default=None)
        if lowest is None:
            return "No deployed device health data is currently available."
        return (
            f"{lowest.label} has the lowest health percentage: {lowest.healthy} of "
            f"{lowest.total} devices are healthy ({(lowest.health_percentage or 0.0):.1f}%)."
        )
    if "distribution" in text or "across all device categories" in text:
        return "; ".join(
            f"{item.label}: {_count_phrase(item)}" for item in summary.categories.values()
        ) + "."
    if re.search(
        r"\battention\b|\bbroken\b|\bwrong\b|\bissues?\b|\bproblems?\b", text
    ):
        # "show me what is broken" wants the priority list, not a health summary.
        issues = []
        for item in deployed:
            if item.faulty:
                issues.append(f"{item.faulty} faulty {item.label}")
            if item.offline:
                issues.append(f"{item.offline} offline {item.label}")
        if summary.open_alerts:
            issues.append(f"{summary.open_alerts} reported open {_plural(summary.open_alerts, 'alert')}")
        return (
            "Priority items are " + ", ".join(issues) + "."
            if issues
            else "No current fleet issue requires attention."
        )

    if summary.total == 0:
        return "No current device health data is available in your authorized scope."
    pct = summary.health_percentage or 0.0
    answer = (
        f"Of {summary.total} monitored devices, {summary.healthy} are healthy ({pct:.1f}%), "
        f"{summary.faulty} {_verb(summary.faulty)} faulty, and "
        f"{summary.offline} {_verb(summary.offline)} offline"
    )
    if summary.unknown:
        answer += f"; {summary.unknown} have unknown status"
    if summary.open_alerts:
        answer += f". The fleet snapshots report {summary.open_alerts} open {_plural(summary.open_alerts, 'alert')}"
    return answer + "."
