"""User directory questions, answered from ThingsBoard under the caller's own scope.

The user-management questions ("how many users", "who logged in today", "which ZO
users are active") are all answerable from what ThingsBoard already returns for a
user page. What they must never do is answer across customers: one tenant-wide call
returns every bank's staff, so the SCOPE decision — not the formatting — is the part
of this module that matters. It is made once, in resolve_directory(), from
/api/auth/user rather than from anything in the question.

Tier (HO / FGMO / ZO) is read from the account's firstName, which the fleet sets to
"Head Office", "BOI NBG" or "BOI ZO". It is a label for grouping answers, never a
permission: nothing here grants access based on it.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# Same display timezone as the alarm answers — one definition, so the two never drift.
from app.query.alarm_answers import IST

HO = "HO"
FGMO = "FGMO"
ZO = "ZO"
OTHER = "OTHER"

_TIER_ORDER = (HO, FGMO, ZO, OTHER)
_TIER_LABEL = {
    HO: "Head Office",
    FGMO: "FGMO/NBG",
    ZO: "ZO",
    OTHER: "other",
}


@dataclass(frozen=True)
class DirectoryUser:
    email: str
    display_name: str
    authority: str
    tier: str
    area: str | None  # "Howrah", "East", ... — the ZO/NBG this account covers
    enabled: bool
    activated: bool
    created_at: datetime | None
    last_login: datetime | None

    @property
    def never_logged_in(self) -> bool:
        return self.last_login is None


def _epoch(value: object) -> datetime | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000, UTC)


def _tier_of(first_name: str, email: str) -> str:
    text = f"{first_name} {email}".lower()
    if "head office" in text or "headoffice" in text or re.search(r"\bho\b", text):
        return HO
    if "nbg" in text or "fgmo" in text:
        return FGMO
    if re.search(r"\bzo\b", text) or ".security@" in email.lower():
        return ZO
    return OTHER


def normalize_user(raw: Mapping[str, Any]) -> DirectoryUser | None:
    email = str(raw.get("email") or "").strip()
    if not email:
        return None
    info = raw.get("additionalInfo")
    info = info if isinstance(info, Mapping) else {}
    first = str(raw.get("firstName") or "").strip()
    last = str(raw.get("lastName") or "").strip()
    tier = _tier_of(first, email)
    # For a "BOI ZO" / "Howrah" pair the area is the lastName; for "Head Office" / "BOI"
    # it is the firstName that carries the role, so there is no area.
    area = last or None if tier in (FGMO, ZO) else None
    return DirectoryUser(
        email=email,
        display_name=" ".join(part for part in (first, last) if part) or email,
        authority=str(raw.get("authority") or "UNKNOWN"),
        tier=tier,
        area=area,
        enabled=info.get("userCredentialsEnabled") is not False,
        activated=info.get("userActivated") is not False,
        created_at=_epoch(raw.get("createdTime")),
        last_login=_epoch(info.get("lastLoginTs")),
    )


def normalize_users(rows: Sequence[Any]) -> list[DirectoryUser]:
    out = []
    for raw in rows:
        if isinstance(raw, Mapping):
            user = normalize_user(raw)
            if user is not None:
                out.append(user)
    return sorted(out, key=lambda u: (_TIER_ORDER.index(u.tier), u.display_name.lower()))


def _time_text(value: datetime) -> str:
    return value.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def _named_area(text: str, users: list[DirectoryUser]) -> list[DirectoryUser]:
    """Users whose area or name appears in the question ("under the MP zone")."""
    matched = [
        user
        for user in users
        if user.area and re.search(rf"\b{re.escape(user.area.lower())}\b", text)
    ]
    return matched


def _listing(items: list[str], limit: int = 15) -> str:
    shown = "; ".join(items[:limit])
    return f"{shown} (showing first {limit} of {len(items)})" if len(items) > limit else shown


def _describe(user: DirectoryUser) -> str:
    line = f"{user.display_name} ({user.email})"
    if user.last_login is not None:
        line += f", last login {_time_text(user.last_login)}"
    else:
        line += ", never logged in"
    return line


def format_user_answer(
    users: list[DirectoryUser], question: str, scope_label: str, now: datetime | None = None
) -> tuple[str, dict[str, Any]]:
    current = now or datetime.now(UTC)
    text = question.lower()
    structured: dict[str, Any] = {
        "scope": scope_label,
        "total": len(users),
        "users": [
            {
                "email": u.email,
                "name": u.display_name,
                "authority": u.authority,
                "tier": u.tier,
                "area": u.area,
                "enabled": u.enabled,
                "activated": u.activated,
                "last_login": u.last_login.isoformat() if u.last_login else None,
            }
            for u in users
        ],
    }

    if not users:
        return f"No users are visible in {scope_label}.", structured

    by_tier = Counter(u.tier for u in users)
    disabled = [u for u in users if not u.enabled or not u.activated]
    never = [u for u in users if u.never_logged_in]

    if "never logged in" in text or "never log in" in text:
        answer = (
            "Every user in " + scope_label + " has logged in at least once."
            if not never
            else "Users who have never logged in: " + _listing([u.display_name for u in never]) + "."
        )
        return answer, structured

    if "logged in today" in text or "login today" in text:
        start = current.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        today = [u for u in users if u.last_login and u.last_login >= start]
        answer = (
            "No user has logged in today (IST)."
            if not today
            else "Logged in today: " + _listing([_describe(u) for u in today]) + "."
        )
        return answer, structured

    if "logged in yesterday" in text:
        end = current.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        rows = [u for u in users if u.last_login and start <= u.last_login < end]
        answer = (
            "No user logged in yesterday (IST)."
            if not rows
            else "Logged in yesterday: " + _listing([_describe(u) for u in rows]) + "."
        )
        return answer, structured

    if "most recently" in text or "logged in recently" in text or "recent login" in text:
        pool = [u for u in users if u.last_login]
        if "zo " in text or " zo" in text:
            pool = [u for u in pool if u.tier == ZO]
        if not pool:
            return f"No login history is recorded for {scope_label}.", structured
        latest = max(pool, key=lambda u: u.last_login or current)
        return f"Most recent login: {_describe(latest)}.", structured

    if "deactivated" in text or "suspended" in text or "locked" in text or "disabled" in text:
        answer = (
            f"No user account in {scope_label} is deactivated or disabled; all "
            f"{len(users)} are active."
            if not disabled
            else "Deactivated or disabled accounts: "
            + _listing([u.display_name for u in disabled])
            + "."
        )
        return answer, structured

    if "admin" in text:
        admins = [u for u in users if u.authority == "TENANT_ADMIN"]
        answer = (
            f"No user in {scope_label} holds tenant-admin access; all {len(users)} are "
            "CUSTOMER_USER accounts."
            if not admins
            else f"{len(admins)} user(s) hold admin access: "
            + _listing([u.display_name for u in admins])
            + "."
        )
        return answer, structured

    if "role" in text or "different role" in text or "authority" in text:
        roles = Counter(u.authority for u in users)
        listed = ", ".join(f"{role} x{count}" for role, count in roles.most_common())
        return f"Roles assigned in {scope_label}: {listed}.", structured

    if "email" in text:
        matched = _named_area(text, users)
        if not matched:
            matched = [u for u in users if u.tier == HO] if re.search(r"\bho\b|head office", text) else []
        if not matched:
            return (
                "Name the zone, region or Head Office and I will give that account's email. "
                f"{len(users)} users are visible in {scope_label}.",
                structured,
            )
        return (
            "; ".join(f"{u.display_name}: {u.email}" for u in matched) + ".",
            structured,
        )

    named = _named_area(text, users)
    if named and ("show me" in text or "user count" in text or "how many" in text or "under" in text):
        if "how many" in text or "count" in text:
            return (
                f"{len(named)} user(s) are registered under that area: "
                + ", ".join(u.display_name for u in named)
                + ".",
                structured,
            )
        return "Users under that area: " + _listing([_describe(u) for u in named]) + ".", structured

    for tier, keywords in ((HO, ("ho ", "head office")), (FGMO, ("fgmo", "nbg")), (ZO, ("zo",))):
        if any(re.search(rf"\b{re.escape(k.strip())}\b", text) for k in keywords):
            rows = [u for u in users if u.tier == tier]
            label = _TIER_LABEL[tier]
            if "how many" in text or "count" in text:
                return f"{len(rows)} {label} user(s) in {scope_label}.", structured
            if not rows:
                return f"No {label} user is visible in {scope_label}.", structured
            return (
                f"{label} users in {scope_label}: "
                + _listing([_describe(u) for u in rows])
                + ".",
                structured,
            )

    if "how many" in text or "count" in text or "total" in text:
        breakdown = ", ".join(
            f"{_TIER_LABEL[tier]} {by_tier[tier]}" for tier in _TIER_ORDER if by_tier[tier]
        )
        return (
            f"{len(users)} users are registered in {scope_label} ({breakdown}). "
            f"{len(users) - len(disabled)} are active.",
            structured,
        )

    breakdown = ", ".join(
        f"{_TIER_LABEL[tier]} {by_tier[tier]}" for tier in _TIER_ORDER if by_tier[tier]
    )
    return (
        f"{len(users)} users are registered in {scope_label} ({breakdown}): "
        + _listing([_describe(u) for u in users])
        + ".",
        structured,
    )
