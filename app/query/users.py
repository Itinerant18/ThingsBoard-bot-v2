"""User directory questions, answered from ThingsBoard under the caller's own scope.

The user-management questions ("how many users", "who logged in today", "which ZO
users are active") are all answerable from what ThingsBoard already returns for a
user page. What they must never do is answer across customers: one tenant-wide call
returns every bank's staff, so the SCOPE decision — not the formatting — is the part
of this module that matters. It is made once, in resolve_directory(), from
/api/auth/user rather than from anything in the question.

An account's LEVEL is read from its name, and every bank on the tenant spells that
differently — BOI writes "BOI ZO Howrah", Bank of Baroda "RO GKOL", Canara "CO
Kolkata", and SBI's branch logins carry no level word at all. So the token is kept as
the bank wrote it and quoted back in answers, while a coarse band (head office /
regional / zonal / branch) orders them and lets a question asked in one bank's
vocabulary find another's level. It is a label for grouping answers, never a
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

# Seniority bands. Each bank spells its levels differently — measured across the
# tenant's real accounts:
#
#   BOI     "BOI NBG East",  "BOI ZO Howrah",  "Head Office BOI"
#   BOB     "HO BOB",        "ZO Kolkata",     "RO GKOL"
#   CANARA  "CO Kolkata",    "ho cb"
#   SBI     "SBI Parihar"    (no level word at all — a branch account)
#
# The TOKEN is what a user is called and is what answers quote back; the BAND only
# orders them and lets one bank's word find another's level when a question uses a
# vocabulary this customer does not ("how many FGMO users" over a fleet that says NBG).
HO = "HO"
REGION = "REGION"
ZONE = "ZONE"
BRANCH = "BRANCH"

_BAND_ORDER = (HO, REGION, ZONE, BRANCH)
_BAND_LABEL = {
    HO: "Head Office",
    REGION: "regional",
    ZONE: "zonal",
    BRANCH: "branch",
}

# Level token -> band. Keys are matched as whole words against the account name.
_LEVEL_BANDS: dict[str, str] = {
    "HEAD OFFICE": HO,
    "HEADOFFICE": HO,
    "HO": HO,
    "CO": REGION,
    "LHO": REGION,
    "NBG": REGION,
    "FGMO": REGION,
    "ZO": ZONE,
    "RO": ZONE,
    "RBO": ZONE,
}

# What a QUESTION's level word means, when the customer has no token spelled that way.
_QUESTION_BANDS: tuple[tuple[str, str], ...] = (
    ("head office", HO),
    ("ho", HO),
    ("fgmo", REGION),
    ("nbg", REGION),
    ("lho", REGION),
    ("circle", REGION),
    ("region", REGION),
    ("zo", ZONE),
    ("ro", ZONE),
    ("rbo", ZONE),
    ("zonal", ZONE),
    ("zone", ZONE),
    ("branch", BRANCH),
)


@dataclass(frozen=True)
class DirectoryUser:
    email: str
    display_name: str
    authority: str
    level: str | None  # the token this bank uses: "NBG", "ZO", "RO", "CO", "HO"
    band: str  # HO / REGION / ZONE / BRANCH, for ordering and cross-bank matching
    area: str | None  # "Howrah", "East", "GKOL" — what this account covers
    enabled: bool
    activated: bool
    created_at: datetime | None
    last_login: datetime | None

    @property
    def never_logged_in(self) -> bool:
        return self.last_login is None

    @property
    def level_label(self) -> str:
        return self.level or _BAND_LABEL[self.band]


def _epoch(value: object) -> datetime | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000, UTC)


def _level_of(*parts: str) -> tuple[str | None, str]:
    """The level token an account name carries, and the band it belongs to.

    Scans firstName AND lastName: banks put the level in either — BOI writes
    "BOI ZO"/"Howrah", one BOI customer writes "BOI"/"HO". "Head Office" is checked
    before "HO" so the two-word form is not read as the bare token plus a stray word.
    An account with no level token is a branch account, which is what SBI's
    "SBI Parihar" accounts are.
    """
    text = " ".join(part for part in parts if part).upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    for token, band in sorted(_LEVEL_BANDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(token)}\b", text):
            return ("HO" if band is HO else token), band
    return None, BRANCH


def _area_of(first: str, last: str, level: str | None) -> str | None:
    """What the account covers, once its level word is removed.

    "BOI ZO"/"Howrah" -> Howrah, "RO"/"GKOL" -> GKOL, "SBI Parihar"/"" -> Parihar.
    Taking lastName blindly gave "BOB" for an account named "HO"/"BOB".
    """
    if level is None:
        # Branch account: the name itself is the area, minus the bank word.
        joined = " ".join(part for part in (first, last) if part)
        stripped = re.sub(r"^\s*(?:sbi|boi|bob|canara|pnb|cb)\b", "", joined, flags=re.IGNORECASE)
        return stripped.strip() or None
    candidates = [part for part in (last, first) if part]
    for candidate in candidates:
        cleaned = re.sub(
            rf"\b(?:{'|'.join(re.escape(k) for k in _LEVEL_BANDS)})\b",
            " ",
            candidate,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\b(?:sbi|boi|bob|canara|pnb|cb)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            return cleaned
    return None


def normalize_user(raw: Mapping[str, Any]) -> DirectoryUser | None:
    email = str(raw.get("email") or "").strip()
    if not email:
        return None
    info = raw.get("additionalInfo")
    info = info if isinstance(info, Mapping) else {}
    first = str(raw.get("firstName") or "").strip()
    last = str(raw.get("lastName") or "").strip()
    level, band = _level_of(first, last)
    return DirectoryUser(
        email=email,
        display_name=" ".join(part for part in (first, last) if part) or email,
        authority=str(raw.get("authority") or "UNKNOWN"),
        level=level,
        band=band,
        area=_area_of(first, last, level),
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
    return sorted(out, key=lambda u: (_BAND_ORDER.index(u.band), u.display_name.lower()))


def _asked_level(text: str) -> tuple[str, str] | None:
    """The level word a question uses, and the band it means. Longest match wins so
    "head office" is not read as the bare "ho"."""
    for word, band in sorted(_QUESTION_BANDS, key=lambda kv: -len(kv[0])):
        # Word-bounded: a bare "ro" otherwise matches inside "from", and any
        # question containing that word gets answered as a level question.
        if re.search(r"\b" + re.escape(word) + r"\b", text):
            return word, band
    return None


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
                "level": u.level,
                "band": u.band,
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

    # Group by the token this bank actually uses, so an answer says "NBG 5" to BOI
    # and "RO 3" to Bank of Baroda rather than imposing one bank's vocabulary.
    by_level = Counter(u.level_label for u in users)
    # Seniority order, not frequency: a reader expects the head office first.
    _rank = {u.level_label: _BAND_ORDER.index(u.band) for u in users}
    level_breakdown = ", ".join(
        f"{label} {count}"
        for label, count in sorted(by_level.items(), key=lambda kv: (_rank[kv[0]], kv[0]))
    )
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
            pool = [u for u in pool if u.band == ZONE]
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
            matched = (
                [u for u in users if u.band == HO]
                if re.search(r"\bho\b|head office", text)
                else []
            )
        if not matched:
            return (
                (
                    "Name the zone, region or Head Office and I will give that "
                    f"account's email. {len(users)} users are visible in {scope_label}."
                ),
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

    asked = _asked_level(text)
    if asked is not None:
        word, band = asked
        # Exact token first: Bank of Baroda has both ZO and RO accounts, so "how many
        # ZO users" must not sweep in the RO ones. The band synonym takes over only
        # when this bank spells the level differently from the question — which is how
        # "how many FGMO users" answers over a fleet whose accounts all say NBG.
        rows = [u for u in users if u.level and u.level.upper() == word.upper()]
        label = word.upper()
        if not rows:
            rows = [u for u in users if u.band == band]
            label = ", ".join(sorted({u.level_label for u in rows})) or _BAND_LABEL[band]
        if "how many" in text or "count" in text:
            return f"{len(rows)} {label} user(s) in {scope_label}.", structured
        if not rows:
            return f"No {_BAND_LABEL[band]} user is visible in {scope_label}.", structured
        return (
            f"{label} users in {scope_label}: "
            + _listing([_describe(u) for u in rows])
            + ".",
            structured,
        )

    if "how many" in text or "count" in text or "total" in text:
        return (
            (
                f"{len(users)} users are registered in {scope_label} "
                f"({level_breakdown}). {len(users) - len(disabled)} are active."
            ),
            structured,
        )

    return (
        f"{len(users)} users are registered in {scope_label} ({level_breakdown}): "
        + _listing([_describe(u) for u in users])
        + ".",
        structured,
    )
