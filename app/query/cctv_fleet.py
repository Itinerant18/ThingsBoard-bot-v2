"""Fleet-wide CCTV recording compliance and camera inventory.

cctv.py answers about ONE branch's NVR. The S-Insights Recording and CCTV Inventory
questions are all fleet-shaped — "across all branches", "which branch has the most
cameras not recording", "what models are deployed" — so they need the same per-device
parsers run over every scoped snapshot and rolled up.

Nothing here fetches. It takes the snapshots the caller is already authorized to see,
which keeps the scope decision in one place (resolved_scope) rather than duplicating
it per report.
"""

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.query import cctv, derived

# A branch NVR is a handful of disks. The largest in this fleet is 21.84 TB across
# four slots, so a three-digit figure is already generous headroom; anything past it
# is a corrupt reading, not a data centre. Bounding per DEVICE rather than on the
# total is deliberate — one bad row must not be able to swamp 97 good ones.
MAX_CREDIBLE_NVR_TB = 500.0


@dataclass
class BranchRecording:
    branch: str
    device_id: str
    total_channels: int = 0
    recording: int = 0  # channels with any recorded footage
    not_recording: int = 0  # channels reporting zero days
    compliant: int = 0  # channels meeting the retention target
    min_days: int | None = None
    max_days: int | None = None
    zero_channels: list[str] = field(default_factory=list)
    # Inventory
    model: str | None = None
    vendor: str | None = None
    cameras_configured: int = 0
    storage_tb: float | None = None
    free_tb: float | None = None
    hdd_error_slots: int = 0
    available: bool = False


@dataclass
class FleetCctv:
    branches: list[BranchRecording]
    retention_days: int

    @property
    def reporting(self) -> list[BranchRecording]:
        """Branches whose NVR actually returned recording data.

        A branch with no NVR payload is NOT a branch with zero cameras — treating it as
        one would quietly deflate every fleet total, so it is excluded from ratios and
        reported separately.
        """
        return [b for b in self.branches if b.available]

    @property
    def silent(self) -> list[BranchRecording]:
        return [b for b in self.branches if not b.available]

    @property
    def total_channels(self) -> int:
        return sum(b.total_channels for b in self.reporting)

    @property
    def recording(self) -> int:
        return sum(b.recording for b in self.reporting)

    @property
    def not_recording(self) -> int:
        return sum(b.not_recording for b in self.reporting)

    @property
    def compliant(self) -> int:
        return sum(b.compliant for b in self.reporting)

    @property
    def cameras_configured(self) -> int:
        return sum(b.cameras_configured for b in self.branches)

    @property
    def recording_percentage(self) -> float | None:
        total = self.total_channels
        return None if total == 0 else self.recording * 100 / total

    @property
    def compliance_percentage(self) -> float | None:
        total = self.total_channels
        return None if total == 0 else self.compliant * 100 / total

    @property
    def credible_storage(self) -> list[BranchRecording]:
        """Branches whose reported capacity is physically possible.

        Live output before this filter: "2987145560790.61 TB of installed recording
        capacity". A single NVR was reporting a corrupt capacity and one bad reading
        dominated the sum, so the whole answer became nonsense while still being
        printed to two decimal places as if measured.
        """
        return [
            b
            for b in self.branches
            if b.storage_tb is not None and 0 < b.storage_tb <= MAX_CREDIBLE_NVR_TB
        ]

    @property
    def implausible_storage(self) -> list[BranchRecording]:
        return [
            b
            for b in self.branches
            if b.storage_tb is not None and b.storage_tb > MAX_CREDIBLE_NVR_TB
        ]

    @property
    def storage_tb(self) -> float:
        return sum(b.storage_tb or 0.0 for b in self.credible_storage)

    @property
    def free_tb(self) -> float:
        # Only from the same branches the total came from, or "consumed" is a
        # subtraction across two different populations.
        return sum(b.free_tb or 0.0 for b in self.credible_storage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retention_days": self.retention_days,
            "branches_reporting": len(self.reporting),
            "branches_without_nvr_data": [b.branch for b in self.silent],
            "total_channels": self.total_channels,
            "recording": self.recording,
            "not_recording": self.not_recording,
            "compliant": self.compliant,
            "recording_percentage": self.recording_percentage,
            "compliance_percentage": self.compliance_percentage,
            "cameras_configured": self.cameras_configured,
            "storage_tb": round(self.storage_tb, 2),
            "free_tb": round(self.free_tb, 2),
            "branch_detail": [
                {
                    "branch": b.branch,
                    "device_id": b.device_id,
                    "total_channels": b.total_channels,
                    "recording": b.recording,
                    "not_recording": b.not_recording,
                    "compliant": b.compliant,
                    "min_days": b.min_days,
                    "max_days": b.max_days,
                    "zero_channels": b.zero_channels,
                    "model": b.model,
                    "vendor": b.vendor,
                    "cameras_configured": b.cameras_configured,
                    "storage_tb": b.storage_tb,
                    "hdd_error_slots": b.hdd_error_slots,
                }
                for b in self.branches
            ],
        }


def _branch_name(raw: Mapping[str, Any], device_id: str) -> str:
    for key in ("branch_name", "branchName", "formattedBranchName", "deviceName"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return device_id


def summarise_branch(
    raw: Mapping[str, Any], device_id: str, retention_days: int = cctv.RETENTION_DAYS
) -> BranchRecording:
    entry = BranchRecording(branch=_branch_name(raw, device_id), device_id=device_id)

    info = cctv.device_info(raw)
    entry.model = info.get("model")
    entry.vendor = info.get("vendor")
    entry.storage_tb = info.get("storage_tb")

    count = derived.camera_count(raw)
    entry.cameras_configured = int(count) if count is not None else 0

    summary = cctv.recording_summary(raw, retention_days)
    if summary.get("available"):
        entry.total_channels = int(summary["total"])
        entry.not_recording = int(summary["zero"])
        entry.recording = entry.total_channels - entry.not_recording
        entry.compliant = int(summary["compliant"])
        entry.min_days = int(summary["min_days"])
        entry.max_days = int(summary["max_days"])
        entry.zero_channels = list(summary["zero_channels"])
    elif entry.cameras_configured:
        # The NVR is reachable and reports installed cameras but published no recording
        # rows at all. That is every camera without footage — a finding to surface, not
        # a branch to drop from the totals as if it had no NVR.
        entry.total_channels = entry.cameras_configured
        entry.not_recording = entry.cameras_configured

    # "Reported something" is the bar, not "reported recordings". A branch that answered
    # with a model and a camera list belongs in the ratios; only true silence is excluded.
    entry.available = bool(summary.get("available") or entry.cameras_configured or info)

    slots = cctv.hdd_info(raw)
    entry.hdd_error_slots = sum(1 for s in slots if str(s.get("status", "")).lower() == "error")
    free = [
        float(s["free_tb"])
        for s in slots
        if str(s.get("free_tb", "")).replace(".", "", 1).isdigit()
    ]
    entry.free_tb = sum(free) if free else None
    return entry


def aggregate_cctv(
    snapshots: Mapping[str, Mapping[str, Any]], retention_days: int = cctv.RETENTION_DAYS
) -> FleetCctv:
    branches = [
        summarise_branch(raw, device_id, retention_days)
        for device_id, raw in sorted(snapshots.items())
    ]
    return FleetCctv(branches=branches, retention_days=retention_days)


def _worst_first(fleet: FleetCctv) -> list[BranchRecording]:
    return sorted(fleet.reporting, key=lambda b: (-b.not_recording, b.branch))


def _listing(items: list[str], limit: int = 12) -> str:
    shown = ", ".join(items[:limit])
    return f"{shown} (showing first {limit} of {len(items)})" if len(items) > limit else shown


def _silent_suffix(fleet: FleetCctv) -> str:
    """Never let unreported branches read as healthy ones."""
    count = len(fleet.silent)
    if not count:
        return ""
    return f" {count} scoped branch(es) returned no NVR data and are excluded from these figures."


# --- Superlative ranking ----------------------------------------------------
#
# Same diagnosis as FleetHealth: "Which branch has the most cameras deployed?" was
# answered with the per-branch recording list, which is real data in an order nobody
# asked for. FleetCctv.branches has carried the per-branch counts all along —
# _worst_first already sorts one of them — so this only picks the column the question
# names and takes the top row. Deterministic sort, no LLM pass.
#
# Deliberately NOT handled: "most Critical risk cameras". Nothing in this module or
# cctv.py defines a risk grade, so ranking on it would mean inventing the definition
# and presenting the result as measured.

_CCTV_MAX = ("most", "highest", "largest", "greatest", "maximum")
_CCTV_MIN = ("fewest", "lowest", "least", "smallest", "minimum")

# (question pattern, row attribute, noun). Order matters: "cameras not recording"
# must be tested before the bare "cameras" deployment match.
_CCTV_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (r"not recording|without footage|zero (?:days|footage)", "not_recording", "channels not recording"),
    (r"non-?compliant", "not_recording", "non-compliant channels"),
    (r"compliant", "compliant", "compliant channels"),
    (r"recording", "recording", "recording channels"),
    (r"channels?", "total_channels", "channels"),
    (r"cameras?", "cameras_configured", "cameras"),
)


def branch_recording_rows(fleet: FleetCctv) -> list[dict[str, object]]:
    """Per-branch recording counts, in the shape area_rollup can sum."""
    return [
        {
            "branch": b.branch,
            "cameras_configured": b.cameras_configured,
            "total_channels": b.total_channels,
            "recording": b.recording,
            "not_recording": b.not_recording,
            "compliant": b.compliant,
        }
        for b in fleet.reporting
    ]


def rank_cctv_branches(fleet: FleetCctv, question: str) -> tuple[str, list[dict[str, object]]] | None:
    """(sentence, ranked rows) when the question asks which branch leads on a column.

    None when it does not, so every existing CCTV answer is untouched.
    """
    text = question.lower()
    if "branch" not in text:
        return None
    wants_max = any(w in text for w in _CCTV_MAX)
    wants_min = any(w in text for w in _CCTV_MIN)
    if wants_max == wants_min:  # neither, or a question asking for both
        return None

    column = noun = None
    for pattern, attr, label in _CCTV_COLUMNS:
        if re.search(pattern, text):
            column, noun = attr, label
            break
    if column is None:
        return None

    candidates = [b for b in fleet.reporting if getattr(b, column, 0) or wants_min]
    if not candidates:
        return None
    # Branch name as tiebreak so the same question always names the same branch.
    candidates.sort(key=lambda b: (getattr(b, column), b.branch), reverse=wants_max)
    top = candidates[0]
    rows = [
        {
            "branch": b.branch,
            "cameras_configured": b.cameras_configured,
            "total_channels": b.total_channels,
            "recording": b.recording,
            "not_recording": b.not_recording,
            "compliant": b.compliant,
        }
        for b in candidates[:10]
    ]
    label = "most" if wants_max else "fewest"
    sentence = (
        f"{top.branch} has the {label} {noun}: {getattr(top, column)}"
        f" of {top.total_channels} channel(s)."
        if column != "cameras_configured"
        else f"{top.branch} has the {label} {noun}: {top.cameras_configured}."
    )
    return sentence + _silent_suffix(fleet), rows


def format_cctv_fleet(fleet: FleetCctv, question: str) -> str:
    text = question.lower()

    if not fleet.reporting and not fleet.cameras_configured:
        return (
            "No CCTV recording data is currently available for any branch in your "
            "authorized scope."
        )

    if "storage" in text or "consumption" in text:
        credible = fleet.credible_storage
        rejected = fleet.implausible_storage
        if not credible:
            return (
                "No branch is reporting a credible recording capacity right now"
                + (
                    f"; {len(rejected)} NVR(s) reported an implausible figure."
                    if rejected
                    else "."
                )
            )
        used = fleet.storage_tb - fleet.free_tb
        answer = (
            f"Across {len(credible)} branches reporting credible capacity, the NVRs "
            f"hold {fleet.storage_tb:.2f} TB, of which {used:.2f} TB is consumed and "
            f"{fleet.free_tb:.2f} TB free."
        )
        if rejected:
            # Named, not silently dropped: a branch whose NVR is talking nonsense is
            # itself worth chasing.
            answer += (
                f" {len(rejected)} NVR(s) reported an implausible capacity and are "
                f"excluded ({_listing([b.branch for b in rejected], 5)})."
            )
        return answer + _silent_suffix(fleet)

    if "model" in text or "inventory" in text or "vendor" in text or "make" in text:
        models = Counter(
            f"{b.vendor} {b.model}" if b.vendor and b.model else (b.model or b.vendor or "unknown")
            for b in fleet.branches
        )
        listed = ", ".join(f"{name} x{count}" for name, count in models.most_common())
        return (
            f"{fleet.cameras_configured} cameras are configured across "
            f"{len(fleet.branches)} scoped branches. NVR models deployed: {listed}."
        )

    if "most cameras" in text and ("not recording" in text or "no recording" in text):
        ranked = _worst_first(fleet)
        if not ranked or ranked[0].not_recording == 0:
            return "Every camera that reports recording data is currently recording."
        top = ranked[0]
        return (
            f"{top.branch} has the most cameras not recording: {top.not_recording} of "
            f"{top.total_channels} channels report zero recorded days "
            f"(channels {_listing(top.zero_channels)})." + _silent_suffix(fleet)
        )

    if "gap" in text or "failure" in text or "not recording" in text:
        offenders = [b for b in fleet.reporting if b.not_recording]
        if not offenders:
            return (
                f"No recording failures: all {fleet.total_channels} reporting channels across "
                f"{len(fleet.reporting)} branches have recorded footage." + _silent_suffix(fleet)
            )
        lines = [
            f"{b.branch}: {b.not_recording} of {b.total_channels} channels "
            f"(channels {_listing(b.zero_channels, 6)})"
            for b in _worst_first(fleet)
            if b.not_recording
        ]
        return (
            f"{fleet.not_recording} of {fleet.total_channels} channels have no recorded "
            f"footage — " + "; ".join(lines) + "." + _silent_suffix(fleet)
        )

    if "percentage" in text or "health" in text or "compliance" in text or "compliant" in text:
        recording_pct = fleet.recording_percentage or 0.0
        compliance_pct = fleet.compliance_percentage or 0.0
        return (
            f"Recording health is {recording_pct:.1f}%: {fleet.recording} of "
            f"{fleet.total_channels} channels across {len(fleet.reporting)} branches are "
            f"recording. Against the {fleet.retention_days}-day retention target, "
            f"{fleet.compliant} channels ({compliance_pct:.1f}%) are compliant."
            + _silent_suffix(fleet)
        )

    if "which" in text and ("recording" in text or "camera" in text):
        recording = [
            f"{b.branch} ({b.recording}/{b.total_channels})"
            for b in fleet.reporting
            if b.recording
        ]
        if not recording:
            return "No camera is currently recording in your authorized scope."
        return "Cameras currently recording, by branch: " + _listing(recording) + "."

    if "how many" in text or "deployed" in text or "count" in text:
        return (
            f"{fleet.cameras_configured} CCTV cameras are configured across "
            f"{len(fleet.branches)} scoped branches; {fleet.total_channels} channels on "
            f"{len(fleet.reporting)} branches report recording data."
        )

    recording_pct = fleet.recording_percentage or 0.0
    return (
        f"Across {len(fleet.reporting)} branches reporting NVR data, {fleet.recording} of "
        f"{fleet.total_channels} channels are recording ({recording_pct:.1f}%), "
        f"{fleet.not_recording} have no footage, and {fleet.compliant} meet the "
        f"{fleet.retention_days}-day retention target." + _silent_suffix(fleet)
    )
