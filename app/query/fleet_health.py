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


_LISTING_RE = re.compile(r"\b(?:show|list|which|what)\b.*\bdevices?\b|\bdevices?\b.*\blist\b")


def category_listing(
    summary: FleetHealthSummary, question: str
) -> tuple[str, list[dict[str, object]]] | None:
    """Branches where one subsystem is deployed, for "show me all IAS devices".

    Five such questions fell through to the 98-branch inventory dump. No ThingsBoard
    call is needed and no device `type` filter would even be right: a branch is ONE
    device and its subsystems are attributes on it, so "all IAS devices" means "the
    branches where IAS is installed" — which aggregate_fleet_health already recorded
    in summary.branches while counting them.
    """
    key = normalize_category(None, question)
    if key is None or not _LISTING_RE.search(question.lower()):
        return None
    label = _LABELS[key]
    rows: list[dict[str, object]] = [
        {"branch": branch, "state": states[label]}
        for branch, states in sorted(summary.branches.items())
        if label in states
    ]
    if not rows:
        return f"No {label} device is deployed in your authorized scope.", []
    shown = ", ".join(f"{r['branch']} ({r['state']})" for r in rows[:15])
    more = f" (showing first 15 of {len(rows)})" if len(rows) > 15 else ""
    return f"{len(rows)} branch(es) with a {label} device: {shown}{more}.", rows


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
    # "Which device is faulty?" / "List all devices that are currently faulty".
    # The per-branch states were already computed and then never surfaced, so these
    # questions were answered with a demand for a device id the operator was asking
    # the bot to find. Naming them is the answer.
    wanted_states = {
        state
        for word, state in (
            ("faulty", "FAULT"),
            ("fault", "FAULT"),
            ("offline", "OFFLINE"),
            ("down", "OFFLINE"),
            ("error", "FAULT"),
            ("not functioning", "FAULT"),
            ("unhealthy", "FAULT"),
        )
        if word in text
    }
    if wanted_states and re.search(r"\bwhich\b|\blist\b|\bshow\b|\bname\b|\bany\b", text):
        hits: list[str] = []
        for branch, modules in sorted(summary.branches.items()):
            bad = [
                label
                for label, state in modules.items()
                if state in wanted_states
                and (category_key is None or label == _LABELS.get(category_key))
            ]
            if bad:
                hits.append(f"{branch} ({', '.join(sorted(bad))})")
        label = " / ".join(sorted(s.lower() for s in wanted_states))
        if not hits:
            return f"No device is currently {label} in your authorized scope."
        shown = "; ".join(hits[:20])
        suffix = f" (showing first 20 of {len(hits)})" if len(hits) > 20 else ""
        return f"{len(hits)} branch(es) with a {label} module: {shown}{suffix}."

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
        # "monitored devices" read as the branch count and contradicted the overview's
        # "98 device(s) in your authorized scope" in the same session. Both numbers are
        # right about different things: 98 branches, each contributing several modules.
        # Say which is being counted rather than leaving the operator to reconcile them.
        f"Across {len(summary.branches)} branches, {summary.total} monitored modules "
        f"(gateway, CCTV, IAS, BAS, FAS, TLS, ACS): {summary.healthy} are healthy ({pct:.1f}%), "
        f"{summary.faulty} {_verb(summary.faulty)} faulty, and "
        f"{summary.offline} {_verb(summary.offline)} offline"
    )
    if summary.unknown:
        answer += f"; {summary.unknown} have unknown status"
    if summary.open_alerts:
        answer += f". The fleet snapshots report {summary.open_alerts} open {_plural(summary.open_alerts, 'alert')}"
    return answer + "."


# --- Superlative ranking ----------------------------------------------------
#
# "Which branch currently has the worst overall health?" was answered with the FLEET
# summary — "Across 98 branches, 153 monitored modules: 68 are healthy (44.4%)..." —
# which never names a branch. 34 of 63 superlative questions in the 2026-07-30
# head-office audit came back as an unranked list or an aggregate.
#
# The per-branch detail was already computed: aggregate_fleet_health fills
# summary.branches with {branch: {module: state}} and then only ever sums it. This
# ranks those rows instead. Deterministic sort, no LLM pass — the answer can only
# contain numbers that are in the rows.

# Direction words, kept separate from polarity. "Which branch has the HIGHEST number
# of offline devices?" asks for the WORST branch — treating "highest" as a best-case
# word ranked it ascending and answered with the healthiest branch. Whether a
# superlative means max or min depends on the metric being good or bad, so the two
# are resolved independently.
_MAX = ("most", "highest", "largest", "greatest", "maximum")
_MIN = ("fewest", "lowest", "least", "smallest", "minimum")
_WORST = ("worst", "poorest")
_BEST = ("best", "healthiest")


def branch_rows(summary: FleetHealthSummary) -> list[dict[str, object]]:
    """Per-branch module counts — the detail the aggregate throws away."""
    rows: list[dict[str, object]] = []
    for branch, modules in summary.branches.items():
        states = list(modules.values())
        total = len(states)
        healthy = sum(1 for s in states if s == NormalizedState.ONLINE.value)
        offline = sum(1 for s in states if s == NormalizedState.OFFLINE.value)
        faulty = sum(1 for s in states if s == NormalizedState.FAULT.value)
        rows.append(
            {
                "branch": branch,
                "total": total,
                "healthy": healthy,
                "offline": offline,
                "faulty": faulty,
                # Modules in UNKNOWN count against health: a branch whose gateway never
                # reported is not healthy, it is unmeasured, and scoring it 100% would
                # put silent branches at the top of a "best" list.
                "health_pct": round(healthy / total * 100, 1) if total else 0.0,
            }
        )
    return rows


def rank_branches(
    summary: FleetHealthSummary, question: str
) -> tuple[str, list[dict[str, object]]] | None:
    """(sentence, ranked rows) when the question asks for a single extreme branch.

    None when it does not, so the caller falls through to the aggregate answer — the
    fleet summary is still the right reply to "what is the overall fleet health?".
    """
    text = question.lower()
    if not any(word in text for word in ("branch", "device", "site")):
        return None

    # Metric first, because it decides which direction a superlative points.
    if "offline" in text or "down" in text:
        key, unit, bad = "offline", "offline", True
    elif "fault" in text or "problem" in text:
        key, unit, bad = "faulty", "faulty", True
    else:
        key, unit, bad = "health_pct", None, False

    asks_worst = any(w in text for w in _WORST)
    asks_best = any(w in text for w in _BEST)
    if asks_worst and asks_best:
        # "Which branch is best and which is worst?" — two questions. Naming one
        # branch would answer half of it and look like the whole answer.
        return None
    if asks_worst:
        descending = bad
    elif asks_best:
        descending = not bad
    elif any(w in text for w in _MAX):
        descending = True
    elif any(w in text for w in _MIN):
        descending = False
    else:
        return None

    rows = [row for row in branch_rows(summary) if row["total"]]
    if not rows:
        return None
    # Name as tiebreak so the same question always returns the same branch.
    rows.sort(key=lambda r: (r[key], r["branch"]), reverse=descending)
    top = rows[0]

    if key == "health_pct":
        label = "best" if descending else "worst"
        sentence = (
            f"{top['branch']} has the {label} overall health: "
            f"{top['healthy']} of {top['total']} modules healthy "
            f"({top['health_pct']}%), {top['offline']} offline, {top['faulty']} faulty."
        )
    else:
        label = "most" if descending else "fewest"
        sentence = (
            f"{top['branch']} has the {label} {unit} modules: {top[key]} of {top['total']}."
        )
    return sentence, rows
