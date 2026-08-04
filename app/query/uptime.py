"""Monthly uptime and fault history, from three ThingsBoard attributes.

    mainDevicesOnTimeData   {SUBSYS: {metric: {lastTs, monthly: {YYYY-MM: {...}}}}}
    mainDevicesFaultData    {SUBSYS: {fault:  {YYYY-MM: {...}}}}
    mainCCTVFaultData       {type:   {chNN:   {lastTs, monthly: {YYYY-MM: {...}}}}}

Three shapes, not one. mainDevicesFaultData has no `monthly` wrapper and no lastTs,
so a single parser written against the first sample silently returns nothing for it.

Uptime percentage is arithmetic, not a guess: month_duration + downtime_minutes sums
to the length of the month (2798.17 + 41841.83 = 44640 = 31 days) on every sample
checked, so month_duration IS the uptime minutes. A test pins that sum.

The *_score fields (uptime_score, idle_score, fault_score, fit_score) are 0-10 values
whose scale nobody has documented to us. They are passed through labelled as scores
and never converted into a percentage or a verdict — inventing that mapping is how a
plausible wrong answer gets made.

Coverage is thin: measured 2026-08-04, mainDevicesOnTimeData is on 23 of 104 BOI
devices, mainDevicesFaultData on 1, mainCCTVFaultData on 5. Every answer states how
many devices actually reported, because "uptime is 62%" over a fifth of the fleet is
a different claim from "uptime is 62%".
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

ONTIME_KEY = "mainDevicesOnTimeData"
DEVICE_FAULT_KEY = "mainDevicesFaultData"
CCTV_FAULT_KEY = "mainCCTVFaultData"
KEYS = (ONTIME_KEY, DEVICE_FAULT_KEY, CCTV_FAULT_KEY)

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


@dataclass
class MonthlyUptime:
    """One subsystem's uptime for one month, on one branch."""

    branch: str
    subsystem: str
    metric: str
    month: str
    uptime_minutes: float
    downtime_minutes: float

    @property
    def total_minutes(self) -> float:
        return self.uptime_minutes + self.downtime_minutes

    @property
    def uptime_pct(self) -> float | None:
        total = self.total_minutes
        return round(self.uptime_minutes / total * 100, 1) if total else None


@dataclass
class MonthlyFault:
    """A fault's recorded duration for one month. NOT uptime — a different measure."""

    branch: str
    subsystem: str
    fault: str
    month: str
    duration_minutes: float
    score: float | None = None
    channel: str | None = None


@dataclass
class UptimeReport:
    uptime: list[MonthlyUptime] = field(default_factory=list)
    device_faults: list[MonthlyFault] = field(default_factory=list)
    cctv_faults: list[MonthlyFault] = field(default_factory=list)
    # Devices in scope, and how many carried each attribute. The gap is the answer's
    # honesty margin.
    devices_seen: int = 0
    devices_with_uptime: int = 0
    devices_with_device_faults: int = 0
    devices_with_cctv_faults: int = 0

    @property
    def months(self) -> list[str]:
        return sorted({row.month for row in self.uptime}, reverse=True)


def _as_obj(value: Any) -> dict[str, Any] | None:
    """The attribute arrives as a JSON string from Redis, or already parsed."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _months_of(node: Mapping[str, Any]) -> dict[str, Any]:
    """The month map, whether or not it sits under a `monthly` wrapper.

    mainDevicesOnTimeData and mainCCTVFaultData wrap it; mainDevicesFaultData does
    not. Detecting the shape beats assuming one and dropping the other silently.
    """
    inner = node.get("monthly")
    if isinstance(inner, Mapping):
        return dict(inner)
    return {k: v for k, v in node.items() if _MONTH_RE.match(str(k))}


def parse_uptime(branch: str, raw: Mapping[str, Any]) -> list[MonthlyUptime]:
    rows: list[MonthlyUptime] = []
    top = _as_obj(raw.get(ONTIME_KEY))
    if not top:
        return rows
    for subsystem, metrics in top.items():
        if not isinstance(metrics, Mapping):
            continue
        for metric, node in metrics.items():
            if not isinstance(node, Mapping):
                continue
            for month, values in _months_of(node).items():
                if not isinstance(values, Mapping):
                    continue
                up = _number(values.get("month_duration"))
                down = _number(values.get("downtime_minutes"))
                if up is None or down is None:
                    continue
                rows.append(
                    MonthlyUptime(branch, str(subsystem), str(metric), str(month), up, down)
                )
    return rows


def parse_device_faults(branch: str, raw: Mapping[str, Any]) -> list[MonthlyFault]:
    rows: list[MonthlyFault] = []
    top = _as_obj(raw.get(DEVICE_FAULT_KEY))
    if not top:
        return rows
    for subsystem, faults in top.items():
        if not isinstance(faults, Mapping):
            continue
        for fault, node in faults.items():
            if not isinstance(node, Mapping):
                continue
            for month, values in _months_of(node).items():
                if not isinstance(values, Mapping):
                    continue
                duration = _number(values.get("month_duration"))
                if duration is None:
                    continue
                rows.append(
                    MonthlyFault(
                        branch, str(subsystem), str(fault), str(month),
                        duration, _number(values.get("fault_score")),
                    )
                )
    return rows


def parse_cctv_faults(branch: str, raw: Mapping[str, Any]) -> list[MonthlyFault]:
    rows: list[MonthlyFault] = []
    top = _as_obj(raw.get(CCTV_FAULT_KEY))
    if not top:
        return rows
    for fault_type, channels in top.items():
        if not isinstance(channels, Mapping):
            continue
        for channel, node in channels.items():
            if not isinstance(node, Mapping):
                continue
            for month, values in _months_of(node).items():
                if not isinstance(values, Mapping):
                    continue
                duration = _number(values.get("month_duration"))
                if duration is None:
                    continue
                rows.append(
                    MonthlyFault(
                        branch, "CCTV", str(fault_type), str(month), duration,
                        _number(values.get("fit_score")), channel=str(channel),
                    )
                )
    return rows


def build_report(snapshots: Mapping[str, Mapping[str, Any]]) -> UptimeReport:
    """Roll the three attributes up across the caller's scoped devices.

    `snapshots` is device_id -> raw field map, exactly what load_fleet_states returns.
    """
    report = UptimeReport(devices_seen=len(snapshots))
    for raw in snapshots.values():
        branch = str(raw.get("branchName") or raw.get("deviceName") or raw.get("sol_id") or "")
        up = parse_uptime(branch, raw)
        df = parse_device_faults(branch, raw)
        cf = parse_cctv_faults(branch, raw)
        report.uptime.extend(up)
        report.device_faults.extend(df)
        report.cctv_faults.extend(cf)
        report.devices_with_uptime += bool(up)
        report.devices_with_device_faults += bool(df)
        report.devices_with_cctv_faults += bool(cf)
    return report


def _coverage(reported: int, seen: int) -> str:
    if reported >= seen:
        return ""
    return (
        f" This covers the {reported} of {seen} branch(es) in your scope that report "
        "this data; the rest do not publish it."
    )


ASKS_UPTIME = re.compile(r"\buptime\b|\bdowntime\b|\bavailability\b|\bon-?time\b")
ASKS_FAULT_HISTORY = re.compile(
    r"\bfault (?:history|score|duration|record)\b|\bfault data\b|\bhow long .*\bfault"
)


def format_uptime_answer(report: UptimeReport, question: str) -> tuple[str, dict[str, Any]] | None:
    """A monthly uptime answer, or None when the question is not about uptime."""
    text = question.lower()
    if not ASKS_UPTIME.search(text):
        return None
    if not report.uptime:
        return (
            (
                "No branch in your scope currently publishes monthly uptime data, so "
                "I cannot report uptime or downtime for them."
            ),
            {"uptime_rows": 0, "devices_seen": report.devices_seen},
        )

    month = report.months[0]
    rows = [row for row in report.uptime if row.month == month]

    # A subsystem named in the question narrows it; otherwise report the fleet.
    named = None
    for subsystem in sorted({row.subsystem for row in rows}, key=len, reverse=True):
        if re.search(rf"\b{re.escape(subsystem.lower())}\b", text):
            named = subsystem
            break
    if named:
        rows = [row for row in rows if row.subsystem == named]

    up = sum(row.uptime_minutes for row in rows)
    down = sum(row.downtime_minutes for row in rows)
    total = up + down
    pct = round(up / total * 100, 1) if total else None
    subject = f"{named} uptime" if named else "Uptime"
    branches = len({row.branch for row in rows if row.branch})

    worst = min(rows, key=lambda r: (r.uptime_pct if r.uptime_pct is not None else 101))
    body = (
        f"{subject} for {month}: {pct}% across {branches} branch(es) "
        f"({up:,.0f} minutes up, {down:,.0f} minutes down)."
        if pct is not None
        else f"{subject} for {month}: no measured minutes recorded."
    )
    if worst.branch and worst.uptime_pct is not None:
        body += f" Lowest is {worst.branch} at {worst.uptime_pct}% ({worst.subsystem})."
    return body + _coverage(report.devices_with_uptime, report.devices_seen), {
        "month": month,
        "subsystem": named,
        "uptime_pct": pct,
        "uptime_minutes": round(up, 2),
        "downtime_minutes": round(down, 2),
        "branches": branches,
        "devices_with_data": report.devices_with_uptime,
        "devices_seen": report.devices_seen,
    }


def format_fault_answer(report: UptimeReport, question: str) -> tuple[str, dict[str, Any]] | None:
    """Recorded fault duration by month. NOT uptime — a separate measure."""
    text = question.lower()
    if not ASKS_FAULT_HISTORY.search(text):
        return None
    wants_cctv = bool(re.search(r"\bcctv\b|\bcamera\b|\bchannel\b", text))
    rows = report.cctv_faults if wants_cctv else report.device_faults
    reported = report.devices_with_cctv_faults if wants_cctv else report.devices_with_device_faults
    label = "CCTV" if wants_cctv else "device"
    if not rows:
        return (
            (
                f"No branch in your scope currently publishes monthly {label} fault "
                "history, so I cannot report it."
            ),
            {"fault_rows": 0, "devices_seen": report.devices_seen},
        )
    month = max(row.month for row in rows)
    current = [row for row in rows if row.month == month]
    total = sum(row.duration_minutes for row in current)
    worst = max(current, key=lambda r: r.duration_minutes)
    where = f"{worst.branch} ({worst.fault}" + (f", {worst.channel})" if worst.channel else ")")
    return (
        f"Recorded {label} fault time for {month}: {total:,.0f} minutes across "
        f"{len(current)} entry(ies). Longest is {where} at "
        f"{worst.duration_minutes:,.0f} minutes."
        + _coverage(reported, report.devices_seen),
        {
            "month": month,
            "kind": label,
            "total_fault_minutes": round(total, 2),
            "entries": len(current),
            "devices_with_data": reported,
            "devices_seen": report.devices_seen,
        },
    )
