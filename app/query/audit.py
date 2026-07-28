"""Audit-log answers for callers who are not tenant administrators.

ThingsBoard exposes audit only at tenant scope: /api/audit/logs returns every
customer's activity in one stream and there is no per-customer endpoint. The bot is
used by HO, LHO, ZO and branch staff across several banks, so refusing everyone
below tenant admin would remove the feature for nearly every real user. Instead the
tenant-wide stream is fetched with an administrator credential and filtered down to
the caller.

That makes this module a security boundary, so it is built to fail closed:

  * An entry is shown only on a POSITIVE match against an allow-list. There is no
    "looks fine, let it through" path — an entry we cannot attribute is dropped.
  * The allow-list is built from the CALLER's own token (their customer's user list,
    their authorized device ids), never from the administrator fetch. Building it
    from the admin's view would reintroduce the leak inside the filter.
  * If the allow-list cannot be built, the sets are empty and every entry is dropped.

Measured against production: of the 500 most recent entries, only 41 carry a real
customerId — ThingsBoard stamps the rest with its null-customer sentinel. Filtering
on customerId alone would therefore hide a customer's own logins from them, which is
why USER and DEVICE entries are attributed by entity id instead.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.query.alarm_answers import IST

# ThingsBoard's "no customer" marker. Treated as UNATTRIBUTED, never as a match.
NULL_CUSTOMER = "13814000-1dd2-11b2-8080-808080808080"

# Entity kinds a customer-scoped caller can ever be shown, when the id also matches.
_USER_ENTITIES = frozenset({"USER"})
_DEVICE_ENTITIES = frozenset({"DEVICE", "ASSET", "ENTITY_VIEW"})


@dataclass(frozen=True)
class AuditEntry:
    entry_id: str
    at: datetime
    user_name: str
    action: str
    status: str
    entity_type: str
    entity_id: str | None
    entity_name: str
    customer_id: str | None
    failure: str

    @property
    def succeeded(self) -> bool:
        return self.status.upper() == "SUCCESS"


@dataclass(frozen=True)
class AuditScope:
    """Everything the caller is allowed to see activity about."""

    customer_id: str | None = None
    user_ids: frozenset[str] = field(default_factory=frozenset)
    device_ids: frozenset[str] = field(default_factory=frozenset)
    unrestricted: bool = False  # tenant admin only


def _ident(value: object) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("id")
    if value in (None, "", NULL_CUSTOMER):
        return None
    return str(value)


def _entity_type(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("entityType") or "")
    return ""


def normalize_entry(raw: Mapping[str, Any]) -> AuditEntry | None:
    created = raw.get("createdTime")
    try:
        at = datetime.fromtimestamp(float(created) / 1000, UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    entity = raw.get("entityId")
    return AuditEntry(
        entry_id=_ident(raw.get("id")) or "",
        at=at,
        user_name=str(raw.get("userName") or "unknown"),
        action=str(raw.get("actionType") or "UNKNOWN"),
        status=str(raw.get("actionStatus") or ""),
        entity_type=_entity_type(entity),
        entity_id=_ident(entity),
        entity_name=str(raw.get("entityName") or ""),
        customer_id=_ident(raw.get("customerId")),
        failure=str(raw.get("actionFailureDetails") or ""),
    )
    # NOTE: actionData is deliberately not carried. It holds clientAddress — another
    # user's IP — and nothing in the answerable questions needs it.


def normalize_entries(rows: Sequence[Any]) -> list[AuditEntry]:
    out = []
    for raw in rows:
        if isinstance(raw, Mapping):
            entry = normalize_entry(raw)
            if entry is not None:
                out.append(entry)
    return out


def visible_to(entry: AuditEntry, scope: AuditScope) -> bool:
    """Whether ONE audit entry may be shown to this caller.

    Allow on positive match only. Every return path below is either an explicit
    match or False; there is deliberately no default-allow branch, so a new
    ThingsBoard entity type is invisible until someone decides it should not be.
    """
    if scope.unrestricted:
        return True
    if scope.customer_id and entry.customer_id == scope.customer_id:
        return True
    if entry.entity_id is None:
        return False
    if entry.entity_type in _USER_ENTITIES:
        return entry.entity_id in scope.user_ids
    if entry.entity_type in _DEVICE_ENTITIES:
        return entry.entity_id in scope.device_ids
    # Tenant-level objects — rule chains, dashboards, widget types, device profiles —
    # belong to the tenant, not to any one customer.
    return False


def filter_entries(entries: Iterable[AuditEntry], scope: AuditScope) -> list[AuditEntry]:
    return [entry for entry in entries if visible_to(entry, scope)]


def _time_text(value: datetime) -> str:
    return value.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def _line(entry: AuditEntry) -> str:
    target = f" on {entry.entity_name}" if entry.entity_name else ""
    outcome = "" if entry.succeeded else f" (FAILURE{': ' + entry.failure if entry.failure else ''})"
    return f"{_time_text(entry.at)} — {entry.user_name} {entry.action}{target}{outcome}"


def _listing(items: list[str], limit: int = 15) -> str:
    shown = "; ".join(items[:limit])
    return f"{shown} (showing first {limit} of {len(items)})" if len(items) > limit else shown


def _truncation_note(truncated: bool, window_label: str) -> str:
    if not truncated:
        return ""
    return (
        f" The audit history for {window_label} is longer than one read, so this "
        "covers only the most recent portion of it."
    )


def format_audit_answer(
    entries: list[AuditEntry],
    question: str,
    scope_label: str,
    window_label: str,
    *,
    truncated: bool = False,
) -> tuple[str, dict[str, Any]]:
    text = question.lower()
    structured: dict[str, Any] = {
        "scope": scope_label,
        "window": window_label,
        "truncated": truncated,
        "count": len(entries),
        "entries": [
            {
                "at": entry.at.isoformat(),
                "user": entry.user_name,
                "action": entry.action,
                "status": entry.status,
                "entity_type": entry.entity_type,
                "entity_name": entry.entity_name,
            }
            for entry in entries
        ],
    }

    if not entries:
        return (
            f"No audit activity within {scope_label} is recorded for {window_label}."
            + _truncation_note(truncated, window_label),
            structured,
        )

    if "failed" in text or "failure" in text:
        failures = [e for e in entries if not e.succeeded]
        answer = (
            f"No failed action is recorded for {window_label} within {scope_label}."
            if not failures
            else f"{len(failures)} failed action(s): " + _listing([_line(e) for e in failures]) + "."
        )
        return answer + _truncation_note(truncated, window_label), structured

    if "logged in" in text or "login" in text or "who logged" in text:
        logins = [e for e in entries if e.action == "LOGIN"]
        answer = (
            f"No login is recorded for {window_label} within {scope_label}."
            if not logins
            else f"{len(logins)} login(s) in {window_label}: "
            + _listing([_line(e) for e in logins])
            + "."
        )
        return answer + _truncation_note(truncated, window_label), structured

    if "configuration change" in text or "last change" in text or "what changed" in text:
        changes = [e for e in entries if e.action not in ("LOGIN", "LOGOUT")]
        if not changes:
            return (
                f"No configuration change is recorded for {window_label} within {scope_label}."
                + _truncation_note(truncated, window_label),
                structured,
            )
        return (
            f"Most recent change: {_line(changes[0])}."
            + _truncation_note(truncated, window_label),
            structured,
        )

    if "action type" in text or "what actions" in text or "distribution" in text:
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.action] = counts.get(entry.action, 0) + 1
        listed = ", ".join(
            f"{name} {count}"
            for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        return (
            f"Audit actions in {window_label} within {scope_label}: {listed}."
            + _truncation_note(truncated, window_label),
            structured,
        )

    if "by whom" in text or "which user" in text or "unique user" in text or "who " in text:
        actors = sorted({entry.user_name for entry in entries})
        return (
            f"{len(actors)} user(s) generated audit activity in {window_label}: "
            + _listing(actors)
            + "."
            + _truncation_note(truncated, window_label),
            structured,
        )

    return (
        f"{len(entries)} audit entries for {window_label} within {scope_label}: "
        + _listing([_line(entry) for entry in entries])
        + "."
        + _truncation_note(truncated, window_label),
        structured,
    )


def window_bounds(hours: int, now: datetime | None = None) -> tuple[int, int]:
    end = now or datetime.now(UTC)
    start = end - timedelta(hours=hours)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
