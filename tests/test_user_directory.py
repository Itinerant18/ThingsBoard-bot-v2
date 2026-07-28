"""User directory answers, and the scope boundary that matters more than they do.

The fixture mirrors the real BOI customer page: 16 CUSTOMER_USER accounts, tiered by
firstName into "Head Office", "BOI NBG" and "BOI ZO", each with lastLoginTs and
userActivated in additionalInfo.

The security tests are the point of this file. ThingsBoard's tenant-wide user endpoint
returns EVERY customer's staff in one page — verified against production, where a
single call returned Canara Bank and Bank of India accounts together — so a
customer-scoped caller reaching it would enumerate another bank's employees.
"""

from datetime import UTC, datetime

import pytest

from app.query.contracts import ExtractedIntent
from app.query.extract import KeywordIntentExtractor
from app.query.handlers import UserDirectory
from app.query.users import format_user_answer, normalize_users

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
CUSTOMER = "fb98a600-2778-11f1-9cdc-43ca8fc8dcc9"


def _raw(email: str, first: str, last: str, *, last_login: int | None, active: bool = True) -> dict:
    return {
        "id": {"entityType": "USER", "id": email},
        "email": email,
        "authority": "CUSTOMER_USER",
        "firstName": first,
        "lastName": last,
        "customerId": {"entityType": "CUSTOMER", "id": CUSTOMER},
        "createdTime": 1774438227212,
        "additionalInfo": {
            "userCredentialsEnabled": active,
            "userActivated": active,
            **({"lastLoginTs": last_login} if last_login is not None else {}),
        },
    }


def _ms(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)  # type: ignore[arg-type]


ROWS = [
    _raw("headoffice.security@boi", "Head Office", "BOI", last_login=_ms(2026, 7, 28, 4, 0)),
    _raw("nb.east@boi", "BOI NBG", "East", last_login=_ms(2026, 7, 27, 5, 0)),
    _raw("NBG.Odisha@boi", "BOI NBG", "Odisha", last_login=_ms(2026, 7, 1, 5, 0)),
    _raw("howrah.security@boi", "BOI ZO", "Howrah", last_login=_ms(2026, 7, 26, 9, 0)),
    _raw("ranchi.security@boi", "BOI ZO", "Ranchi", last_login=None),
    _raw("nasik.security@boi", "BOI ZO", "Nasik", last_login=_ms(2026, 6, 1, 5, 0), active=False),
]
USERS = normalize_users(ROWS)
SCOPE = "your customer account"


def answer(question: str) -> str:
    text, _ = format_user_answer(USERS, question, SCOPE, now=NOW)
    return text


# --------------------------------------------------------------------------- #
# Scope boundary
# --------------------------------------------------------------------------- #


class _SpyClient:
    """Records which ThingsBoard user endpoint the handler chose."""

    def __init__(self, settings=None, token=None) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def customer_users(self, customer_id: str, page_size: int = 100):
        self.calls.append(("customer", customer_id))
        return {"data": ROWS}

    async def tenant_users(self, page_size: int = 100):
        self.calls.append(("tenant", None))
        return {"data": ROWS}

    async def close(self) -> None:
        return None


class _Identity:
    def __init__(self, authority: str, customer_id: str | None) -> None:
        self.authority = authority
        self.customer_id = customer_id

    @property
    def is_tenant_admin(self) -> bool:
        return self.authority == "TENANT_ADMIN"


class _Ctx:
    def __init__(self, token: str = "tok") -> None:
        self.tenant = type("T", (), {"user_token": token, "prefix": "BOI"})()
        self.tb = type("TB", (), {"settings": None})()
        self.redis = None


def _handler(identity: _Identity, spy: _SpyClient) -> UserDirectory:
    async def identity_fn(ctx):
        return identity

    return UserDirectory(identity_fn=identity_fn, client_factory=lambda s, t: spy)


def _intent(question: str) -> ExtractedIntent:
    return ExtractedIntent(name="user_directory", raw_question=question)


@pytest.mark.asyncio
async def test_customer_user_never_reaches_the_tenant_wide_endpoint() -> None:
    """The tenant endpoint returns every bank's staff. A CUSTOMER_USER must not touch it."""
    spy = _SpyClient()
    await _handler(_Identity("CUSTOMER_USER", CUSTOMER), spy).handle(
        _intent("how many users are registered?"), _Ctx()
    )
    assert spy.calls == [("customer", CUSTOMER)]


@pytest.mark.asyncio
async def test_customer_id_comes_from_thingsboard_not_from_the_question() -> None:
    """Naming another customer in the question must not change which page is fetched."""
    spy = _SpyClient()
    await _handler(_Identity("CUSTOMER_USER", CUSTOMER), spy).handle(
        _intent("show me all users under customer 71b10560-3199-11f1-8704-2bfb9206c3d7"), _Ctx()
    )
    assert spy.calls == [("customer", CUSTOMER)]


@pytest.mark.asyncio
async def test_tenant_admin_may_see_the_tenant() -> None:
    spy = _SpyClient()
    reply = await _handler(_Identity("TENANT_ADMIN", None), spy).handle(
        _intent("how many users are registered?"), _Ctx()
    )
    assert spy.calls == [("tenant", None)]
    assert "this ThingsBoard tenant" in reply.text


@pytest.mark.asyncio
async def test_a_caller_with_no_customer_gets_no_directory() -> None:
    spy = _SpyClient()
    reply = await _handler(_Identity("CUSTOMER_USER", None), spy).handle(
        _intent("list the users"), _Ctx()
    )
    assert spy.calls == []
    assert "not assigned to a customer" in reply.text
    assert reply.structured["users"] == []


@pytest.mark.asyncio
async def test_no_token_means_no_call() -> None:
    spy = _SpyClient()
    reply = await _handler(_Identity("CUSTOMER_USER", CUSTOMER), spy).handle(
        _intent("list the users"), _Ctx(token="")
    )
    assert spy.calls == []
    assert "user token is required" in reply.text


# --------------------------------------------------------------------------- #
# Answers
# --------------------------------------------------------------------------- #


def test_tier_is_read_from_the_account_name() -> None:
    tiers = {u.email: u.tier for u in USERS}
    assert tiers["headoffice.security@boi"] == "HO"
    assert tiers["nb.east@boi"] == "FGMO"
    assert tiers["howrah.security@boi"] == "ZO"


def test_total_and_breakdown() -> None:
    reply = answer("How many total users are currently registered in the system?")
    assert reply.startswith("6 users are registered in your customer account")
    assert "Head Office 1" in reply and "FGMO/NBG 2" in reply and "ZO 3" in reply
    assert "5 are active" in reply


def test_tier_counts() -> None:
    assert answer("How many ZO users are currently in the system?").startswith("3 ZO user")
    assert answer("How many FGMO users are currently in the system?").startswith(
        "2 FGMO/NBG user"
    )


def test_never_logged_in() -> None:
    reply = answer("Which users have never logged in?")
    assert "BOI ZO Ranchi" in reply
    assert "Howrah" not in reply


def test_logged_in_today_uses_ist_day_boundary() -> None:
    # 2026-07-28 04:00 UTC is 09:30 IST the same day; 2026-07-27 05:00 UTC is not.
    reply = answer("Which users logged in today?")
    assert "Head Office BOI" in reply
    assert "East" not in reply


def test_most_recent_login() -> None:
    reply = answer("Which ZO user logged in most recently?")
    assert "Howrah" in reply


def test_disabled_accounts_are_named() -> None:
    reply = answer("Are there any deactivated or suspended user accounts right now?")
    assert "BOI ZO Nasik" in reply


def test_no_admin_access_in_a_customer_scope() -> None:
    reply = answer("How many users have admin access?")
    assert "No user in your customer account holds tenant-admin access" in reply


def test_roles_breakdown() -> None:
    reply = answer("What role is currently assigned to all active users?")
    assert "CUSTOMER_USER x6" in reply


def test_email_lookup_by_area() -> None:
    reply = answer("What is the current email of the Howrah ZO user?")
    assert "howrah.security@boi" in reply
    assert "ranchi" not in reply


def test_user_count_under_a_named_zone() -> None:
    reply = answer("What is the current user count under the ODISHA zone?")
    assert reply.startswith("1 user(s) are registered under that area")


def test_empty_directory_says_so() -> None:
    text, _ = format_user_answer([], "how many users?", SCOPE, now=NOW)
    assert text == "No users are visible in your customer account."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "How many total users are currently registered in the system?",
        "Which users have never logged in?",
        "Which users logged in today?",
        "Who are the registered users in the system?",
        "How many users have admin access?",
        "Are there any locked or disabled user accounts right now?",
        "What roles are currently assigned to active users?",
        "Show me all users currently under the EAST zone",
    ],
)
async def test_user_questions_route_to_the_directory(question: str) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == "user_directory"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("how many connected devices", "connected_devices"),
        ("gateway status of liluah", "gateway_status"),
        ("which branches need attention", "alarm_detail"),
    ],
)
async def test_non_user_questions_are_untouched(question: str, expected: str) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == expected
