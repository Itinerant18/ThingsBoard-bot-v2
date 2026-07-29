"""Audit answers, and the filter that stands between one bank and another's activity.

ThingsBoard has no per-customer audit endpoint, so the tenant-wide stream is read
with an administrator credential and reduced per caller. Every test below that fails
means one customer can see another's activity, so the fixture deliberately mixes:

  * entries for the caller's own customer, carrying a real customerId
  * entries for a DIFFERENT customer
  * USER logins stamped with ThingsBoard's null-customer sentinel — 92% of the real
    stream looks like this, so it is the shape that decides whether the filter works
  * tenant-level objects (rule chains, dashboards) belonging to no customer at all
"""

from datetime import UTC, datetime

import pytest

from app.auth.tb_acl import PermissionCheckUnavailable
from app.query.audit import (
    NULL_CUSTOMER,
    AuditScope,
    filter_entries,
    format_audit_answer,
    normalize_entries,
    visible_to,
)
from app.query.contracts import ExtractedIntent
from app.query.extract import KeywordIntentExtractor
from app.query.handlers import AuditLog

MINE = "fb98a600-2778-11f1-9cdc-43ca8fc8dcc9"  # Bank of India
THEIRS = "71b10560-3199-11f1-8704-2bfb9206c3d7"  # Canara Bank

MY_USER = "0597e4c0-283e-11f1-afd7-eb430bfb427f"
THEIR_USER = "02c0f150-705b-11f1-8c55-5bb8284c6aef"
MY_DEVICE = "01c61bb0-ab4c-11f0-91df-7ffa16af2ee9"
THEIR_DEVICE = "04de5640-a5bc-11f0-b150-2710a8915e1d"


def _ms(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)  # type: ignore[arg-type]


def _entry(
    entity_type: str,
    entity_id: str,
    name: str,
    user: str,
    *,
    customer: str = NULL_CUSTOMER,
    action: str = "LOGIN",
    status: str = "SUCCESS",
    at: int = _ms(2026, 7, 28, 6, 0),
) -> dict:
    return {
        "id": {"id": f"{entity_id}:{at}"},
        "createdTime": at,
        "tenantId": {"entityType": "TENANT", "id": "24d74bb0-2061-11ee-86d5-f58fb189657b"},
        "customerId": {"entityType": "CUSTOMER", "id": customer},
        "entityId": {"entityType": entity_type, "id": entity_id},
        "entityName": name,
        "userId": {"entityType": "USER", "id": entity_id},
        "userName": user,
        "actionType": action,
        # Present in the real payload and deliberately never carried into an answer.
        "actionData": {"clientAddress": "115.246.136.44", "browser": "Other"},
        "actionStatus": status,
        "actionFailureDetails": "" if status == "SUCCESS" else "Bad credentials",
    }


RAW = [
    _entry("USER", MY_USER, "ranchi.security@boi", "ranchi.security@boi"),
    _entry("USER", THEIR_USER, "securitycokol@canarabank", "securitycokol@canarabank"),
    _entry("DEVICE", MY_DEVICE, "BOI-MALDATOWN", "ranchi.security@boi", action="UPDATED"),
    _entry("DEVICE", THEIR_DEVICE, "BOI-R-BAZAR", "securitycokol@canarabank", action="UPDATED"),
    _entry("RULE_CHAIN", "rc-1", "Root Rule Chain", "info@seple.in", action="UPDATED"),
    _entry("DASHBOARD", "db-1", "Tenant Dashboard", "info@seple.in", action="UPDATED"),
    _entry("ASSET", "asset-9", "Some Asset", "someone@elsewhere", action="ADDED"),
    _entry(
        "USER",
        MY_USER,
        "ranchi.security@boi",
        "ranchi.security@boi",
        customer=MINE,
        status="FAILURE",
        at=_ms(2026, 7, 27, 6, 0),
    ),
]
ENTRIES = normalize_entries(RAW)
SCOPE = AuditScope(
    customer_id=MINE,
    user_ids=frozenset({MY_USER}),
    device_ids=frozenset({MY_DEVICE}),
)


# --------------------------------------------------------------------------- #
# The filter
# --------------------------------------------------------------------------- #


def test_only_the_callers_own_activity_survives() -> None:
    visible = filter_entries(ENTRIES, SCOPE)
    names = {entry.entity_name for entry in visible}
    assert names == {"ranchi.security@boi", "BOI-MALDATOWN"}


def test_another_customers_login_is_dropped() -> None:
    theirs = next(e for e in ENTRIES if e.entity_id == THEIR_USER)
    assert visible_to(theirs, SCOPE) is False


def test_another_customers_device_change_is_dropped() -> None:
    theirs = next(e for e in ENTRIES if e.entity_id == THEIR_DEVICE)
    assert visible_to(theirs, SCOPE) is False


def test_tenant_level_objects_are_invisible_to_a_customer() -> None:
    for entity_type in ("RULE_CHAIN", "DASHBOARD"):
        entry = next(e for e in ENTRIES if e.entity_type == entity_type)
        assert visible_to(entry, SCOPE) is False


def test_a_null_customer_login_is_attributed_by_user_id_not_dropped() -> None:
    """92% of real entries carry the null sentinel. Filtering on customerId alone
    would hide a customer's own logins from them."""
    mine = next(e for e in ENTRIES if e.entity_id == MY_USER and e.customer_id is None)
    assert mine.customer_id is None
    assert visible_to(mine, SCOPE) is True


def test_an_unknown_entity_type_is_dropped_not_allowed() -> None:
    """No default-allow path: a ThingsBoard entity kind nobody has considered yet
    must be invisible until someone decides otherwise."""
    entry = normalize_entries([_entry("OTA_PACKAGE", "ota-1", "fw", "info@seple.in")])[0]
    assert visible_to(entry, SCOPE) is False


def test_an_empty_allow_list_shows_nothing() -> None:
    """The state after a failed allow-list build must be deny-all, never allow-all."""
    assert filter_entries(ENTRIES, AuditScope(customer_id=None)) == []


def test_a_tenant_admin_sees_everything() -> None:
    assert len(filter_entries(ENTRIES, AuditScope(unrestricted=True))) == len(ENTRIES)


def test_client_ip_is_never_carried_into_an_answer() -> None:
    text, structured = format_audit_answer(
        filter_entries(ENTRIES, SCOPE),
        "show me the audit logs",
        "your customer account",
        "the last week",
        scope=SCOPE,
    )
    assert "115.246.136.44" not in text
    assert "115.246.136.44" not in str(structured)


# --------------------------------------------------------------------------- #
# The handler's scope decision
# --------------------------------------------------------------------------- #


class _Identity:
    def __init__(self, authority: str, customer_id: str | None) -> None:
        self.authority = authority
        self.customer_id = customer_id

    @property
    def is_tenant_admin(self) -> bool:
        return self.authority == "TENANT_ADMIN"


class _CallerClient:
    def __init__(self, users_fail: bool = False) -> None:
        self.calls: list[str] = []
        self._users_fail = users_fail

    async def customer_users(self, customer_id: str, page_size: int = 100):
        self.calls.append("customer_users")
        if self._users_fail:
            raise RuntimeError("thingsboard unavailable")
        return {"data": [{"id": {"entityType": "USER", "id": MY_USER}}]}

    async def audit_logs(self, start_ts: int, end_ts: int, page_size: int = 100):
        self.calls.append("audit_logs_as_caller")
        return {"data": RAW, "truncated": False}

    async def close(self) -> None:
        return None


class _ServiceTb:
    """The admin-credentialled service client the handler reads the stream with."""

    settings = None

    def __init__(self, truncated: bool = False) -> None:
        self.calls = 0
        self._truncated = truncated

    async def audit_logs(self, start_ts: int, end_ts: int, page_size: int = 100):
        self.calls += 1
        return {"data": RAW, "truncated": self._truncated}


class _Ctx:
    def __init__(self, tb: _ServiceTb, token: str = "tok") -> None:
        self.tenant = type("T", (), {"user_token": token, "prefix": "BOI"})()
        self.tb = tb
        self.redis = None
        self.db = None


async def _scope_fn(ctx):
    return type("S", (), {"tb_device_ids": [MY_DEVICE]})()


def _handler(identity: _Identity, caller: _CallerClient) -> AuditLog:
    async def identity_fn(ctx):
        return identity

    return AuditLog(
        identity_fn=identity_fn,
        client_factory=lambda s, t: caller,
        scope_fn=_scope_fn,
    )


def _intent(question: str) -> ExtractedIntent:
    return ExtractedIntent(name="audit_log", raw_question=question)


@pytest.mark.asyncio
async def test_customer_caller_sees_only_their_own_entries_end_to_end() -> None:
    caller, tb = _CallerClient(), _ServiceTb()
    reply = await _handler(_Identity("CUSTOMER_USER", MINE), caller).handle(
        _intent("show me the audit logs"), _Ctx(tb)
    )
    assert tb.calls == 1
    assert "BOI-MALDATOWN" in reply.text
    assert "canarabank" not in reply.text
    assert "Root Rule Chain" not in reply.text
    assert reply.structured["count"] == 3


@pytest.mark.asyncio
async def test_allow_list_failure_refuses_rather_than_serving_unfiltered() -> None:
    """The single most dangerous failure mode: if the caller's user list cannot be
    built, the handler must not fall through to the unfiltered tenant stream."""
    caller, tb = _CallerClient(users_fail=True), _ServiceTb()
    with pytest.raises(PermissionCheckUnavailable):
        await _handler(_Identity("CUSTOMER_USER", MINE), caller).handle(
            _intent("show me the audit logs"), _Ctx(tb)
        )
    assert tb.calls == 0  # the tenant stream was never even fetched


@pytest.mark.asyncio
async def test_tenant_admin_reads_under_their_own_token_not_the_service_account() -> None:
    caller, tb = _CallerClient(), _ServiceTb()
    reply = await _handler(_Identity("TENANT_ADMIN", None), caller).handle(
        _intent("show me the audit logs"), _Ctx(tb)
    )
    assert "audit_logs_as_caller" in caller.calls
    assert tb.calls == 0
    assert reply.structured["count"] == len(RAW)


@pytest.mark.asyncio
async def test_caller_with_no_customer_gets_nothing() -> None:
    caller, tb = _CallerClient(), _ServiceTb()
    reply = await _handler(_Identity("CUSTOMER_USER", None), caller).handle(
        _intent("show me the audit logs"), _Ctx(tb)
    )
    assert tb.calls == 0
    assert "not assigned to a customer" in reply.text


@pytest.mark.asyncio
async def test_truncation_is_disclosed_not_reported_as_a_clean_result() -> None:
    caller, tb = _CallerClient(), _ServiceTb(truncated=True)
    reply = await _handler(_Identity("CUSTOMER_USER", MINE), caller).handle(
        _intent("show me the audit logs"), _Ctx(tb)
    )
    assert "only the most recent portion" in reply.text


# --------------------------------------------------------------------------- #
# Answers and routing
# --------------------------------------------------------------------------- #


def answer(question: str) -> str:
    text, _ = format_audit_answer(
        filter_entries(ENTRIES, SCOPE),
        question,
        "your customer account",
        "the last week",
        scope=SCOPE,
    )
    return text


def test_failed_actions() -> None:
    reply = answer("Show me the failed logins")
    assert "1 failed action(s)" in reply
    assert "Bad credentials" in reply


def test_who_logged_in() -> None:
    reply = answer("Who logged in recently?")
    assert "ranchi.security@boi" in reply
    assert "canarabank" not in reply


def test_last_configuration_change() -> None:
    reply = answer("When was the last configuration change?")
    assert "BOI-MALDATOWN" in reply and "UPDATED" in reply


def test_no_activity_reads_as_scoped_not_as_empty_system() -> None:
    text, _ = format_audit_answer(
        [], "show me the audit logs", "your customer account", "today", scope=SCOPE
    )
    assert text == "No audit activity within your customer account is recorded for today."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "Show me the audit logs",
        "Show me audit logs for today",
        "What changes were made in the system and by whom?",
        "When was the last configuration change?",
        "What actions were performed by a specific user?",
    ],
)
async def test_audit_questions_route_to_the_audit_handler(question: str) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == "audit_log"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    ["Which users have never logged in?", "How many total users are registered?"],
)
async def test_directory_questions_do_not_become_audit_questions(question: str) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == "user_directory"


# --------------------------------------------------------------------------- #
# Disclosure policy: no credentials, no other tenant's people
# --------------------------------------------------------------------------- #


def test_an_outside_actor_is_masked_but_the_action_is_still_shown() -> None:
    """The integrator acting on a bank's device is something the bank must see; the
    integrator's staff directory is not. Live output named
    "romen.halder@seple.in TIMESERIES_DELETED on BOI-LILUAH" to a BOI operator."""
    raw = _entry(
        "DEVICE", MY_DEVICE, "BOI-LILUAH", "romen.halder@seple.in", action="TIMESERIES_DELETED"
    )
    raw["userId"] = {"entityType": "USER", "id": "outsider-1"}
    entries = normalize_entries([raw])
    text, structured = format_audit_answer(
        entries, "show me the audit logs", "your customer account", "the last week", scope=SCOPE
    )
    assert "romen.halder" not in text
    assert "romen.halder" not in str(structured)
    assert "outside your organisation" in text
    assert "seple.in" in text  # the org is named, the person is not
    assert "TIMESERIES_DELETED" in text and "BOI-LILUAH" in text


def test_a_tenant_admin_still_sees_who_acted() -> None:
    raw = _entry("DEVICE", MY_DEVICE, "BOI-LILUAH", "romen.halder@seple.in", action="UPDATED")
    raw["userId"] = {"entityType": "USER", "id": "outsider-1"}
    text, _ = format_audit_answer(
        normalize_entries([raw]),
        "show me the audit logs",
        "this ThingsBoard tenant",
        "the last week",
        scope=AuditScope(unrestricted=True),
    )
    assert "romen.halder@seple.in" in text


def test_the_callers_own_colleagues_are_not_masked() -> None:
    text, _ = format_audit_answer(
        filter_entries(ENTRIES, SCOPE),
        "who logged in recently?",
        "your customer account",
        "the last week",
        scope=SCOPE,
    )
    assert "ranchi.security@boi" in text
