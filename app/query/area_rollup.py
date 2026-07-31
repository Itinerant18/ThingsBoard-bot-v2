"""Roll per-branch rows up to their zone or region, and rank the result.

FleetHealth and CctvFleet already compute a row per branch. The hierarchy tree
already knows which area each branch sits under. Zone and region questions needed
neither new ThingsBoard calls nor new metrics — only the join between the two, which
nothing was doing.

Deliberately NOT here: any metric the fleet does not record. "Which zone has the best
SLA compliance?" cannot be answered by summing recording percentages, and answering
it that way would be inventing a definition of SLA. Those decline instead.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.query.hierarchy_answers import Node, ScopedTree

# Region questions want the top level ("NBG EAST"); zone questions want the level
# directly above the branch ("ZO HOWRAH"). Both words are one bank's vocabulary, so
# the level is chosen by what the question asks for, never by a fixed depth.
_ASKS_REGION = re.compile(r"\bregions?\b|\bnbg\b|\bfgmo\b|\bcircles?\b")

# Metrics the fleet genuinely does not record. Answering these by summing something
# adjacent would be inventing the definition.
UNRECORDED = {
    "sla": (
        r"\bsla\b|\bservice level\b|\buptime target\b",
        (
            "SLA thresholds and compliance are not among the data this fleet "
            "publishes, so I cannot rank areas by them. I can rank by health, "
            "offline modules, open alarms or recording compliance instead."
        ),
    ),
    "risk": (
        r"\bcritical risk\b|\brisk (?:grade|score|level|categor)",
        (
            "No risk grade is recorded for cameras in this fleet, so I cannot rank "
            "areas by it. I can rank by cameras not recording, or by recording "
            "compliance."
        ),
    ),
}


def unrecorded_metric(question: str) -> str | None:
    """The explanation to give when a question names a metric nothing records."""
    text = question.lower()
    for pattern, message in UNRECORDED.values():
        if re.search(pattern, text):
            return message
    return None


def area_of_branch(tree: ScopedTree, question: str) -> dict[str, str]:
    """branch display name -> the area it belongs to, at the level the question wants.

    Keyed by display name because that is what the per-branch rows carry; the rows
    come from telemetry snapshots, which never saw a node_id.
    """
    want_region = bool(_ASKS_REGION.search(question.lower()))
    root = tree.root
    mapping: dict[str, str] = {}
    for leaf in tree.leaves:
        ancestors = [
            tree.nodes[node_id]
            for node_id in tree.ancestors.get(leaf.node_id, ())
            if node_id in tree.nodes and node_id != leaf.node_id
        ]
        ancestors = [n for n in ancestors if root is None or n.node_id != root.node_id]
        if not ancestors:
            continue
        ancestors.sort(key=lambda n: n.level)
        chosen: Node = ancestors[0] if want_region else ancestors[-1]
        mapping[leaf.display_name] = chosen.display_name
    return mapping


def _branch_keys(row: Mapping[str, Any]) -> str:
    for key in ("branch", "name"):
        if key in row:
            return str(row[key])
    return ""


def roll_up(
    rows: Sequence[Mapping[str, Any]], area_of: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Sum every numeric column of the per-branch rows, grouped by area.

    Branch names in telemetry ("BRANCH HOWRAH") and in the hierarchy ("BOI-HOWRAH")
    do not always match character for character, so matching falls back to the
    distinguishing words. A branch that still cannot be placed is dropped rather than
    bucketed into a wrong area — an area total that silently includes strangers is
    worse than one that is short.
    """
    normalized = {_words(name): area for name, area in area_of.items()}
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        branch = _branch_keys(row)
        area = area_of.get(branch) or normalized.get(_words(branch))
        if not area:
            continue
        bucket = grouped.setdefault(area, {"area": area, "branches": 0})
        bucket["branches"] += 1
        for key, value in row.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            bucket[key] = bucket.get(key, 0) + value
    for bucket in grouped.values():
        total = bucket.get("total") or 0
        healthy = bucket.get("healthy") or 0
        if total:
            bucket["health_pct"] = round(healthy / total * 100, 1)
        channels = bucket.get("total_channels") or 0
        recording = bucket.get("recording") or 0
        if channels:
            bucket["recording_pct"] = round(recording / channels * 100, 1)
    return list(grouped.values())


_MAX = ("most", "highest", "largest", "greatest", "maximum")
_MIN = ("fewest", "lowest", "least", "smallest", "minimum")
_WORST = ("worst", "poorest")
_BEST = ("best", "healthiest")

# (question pattern, column, noun, is a bad thing to have a lot of).
# Order matters: the more specific phrasing is tested first.
_COLUMNS: tuple[tuple[str, str, str, bool], ...] = (
    (r"non-?compliant|not recording|without footage", "not_recording", "channels not recording", True),
    (r"recording compliance|compliance", "recording_pct", "recording compliance", False),
    (r"cameras?|channels?", "cameras_configured", "cameras", False),
    (r"offline|down", "offline", "offline modules", True),
    (r"fault|problem", "faulty", "faulty modules", True),
    (r"branch(?:es)?", "branches", "branches", False),
    (r"performance|health|overall", "health_pct", "overall health", False),
)


def rank_areas(
    rows: Sequence[Mapping[str, Any]], question: str
) -> tuple[str, list[dict[str, Any]]] | None:
    """(sentence, ranked rows) when the question asks which area leads on a column.

    Same polarity rule as rank_branches: whether a superlative points at max or min
    depends on the metric, not the adjective. "Which zone has the HIGHEST number of
    offline modules" asks for the worst zone.
    """
    text = question.lower()
    usable = [dict(row) for row in rows if row.get("area")]
    if not usable:
        return None

    column = noun = None
    bad = False
    for pattern, key, label, is_bad in _COLUMNS:
        if re.search(pattern, text) and any(key in row for row in usable):
            column, noun, bad = key, label, is_bad
            break
    if column is None:
        return None

    asks_worst = any(word in text for word in _WORST)
    asks_best = any(word in text for word in _BEST)
    if asks_worst and asks_best:
        return None
    if asks_worst:
        descending = bad
    elif asks_best:
        descending = not bad
    elif any(word in text for word in _MAX):
        descending = True
    elif any(word in text for word in _MIN):
        descending = False
    else:
        return None

    usable = [row for row in usable if column in row]
    if not usable:
        return None
    usable.sort(key=lambda row: (row[column], row["area"]), reverse=descending)
    top = usable[0]
    value = top[column]
    unit = "%" if column.endswith("_pct") else ""
    lead = "highest" if descending else "lowest"
    if column in ("health_pct", "recording_pct"):
        lead = "best" if descending else "worst"
    sentence = (
        f"{top['area']} has the {lead} {noun}: {value}{unit}, "
        f"across {top.get('branches', 0)} branch(es)."
    )
    return sentence, usable


def _words(value: str) -> str:
    """Distinguishing words of a branch name, for matching across sources."""
    text = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    text = re.sub(r"\b(?:branch|boi|the)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()
