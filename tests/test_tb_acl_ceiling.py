"""v2's device scope must never exceed what ThingsBoard authorizes.

The bug this pins, measured against production on 2026-07-27 with a genuine
CUSTOMER_USER token for BOI-MALDATOWN:

    ThingsBoard authorized  100 devices
    v2 scoped the user to   104 devices
    excess                    6  (BOI-BAS, BOI-DX7, BOI-BAHALDA,
                                  BOI-LOHARDAGA-CC, BOI-DX5, BOI-R-BAZAR)

Cause: v2 scoped by customer PREFIX ("BOI"), which aggregates five ThingsBoard
customers, while ThingsBoard scopes by customer. No forged token involved.

These tests exercise the CHAT path (handlers/_default_scope and the branch-name
gate), not just the HTTP dependency. The original bug lived in chat while the HTTP
endpoint used a separate code path, so a test that only covered deps would have
passed while the reported leak survived.
"""

from types import SimpleNamespace

import pytest

from app.auth import scope_resolver
from app.auth.jwt import TenantContext
from app.auth.scope_resolver import PermissionCheckUnavailable, resolved_scope
from app.config import Settings
from app.hierarchy.scope import ScopedBranches

# 104 local hierarchy devices; ThingsBoard authorizes only the first 100.
LOCAL_IDS = [f"dev-{i:03d}" for i in range(104)]
TB_AUTHORIZED = frozenset(LOCAL_IDS[:100])
EXCESS = set(LOCAL_IDS[100:])

SETTINGS = Settings(database_url="postgresql+asyncpg://unused/unused")

TENANT = TenantContext(
    tenant_id="24d74bb0-2061-11ee-86d5-f58fb189657b",
    customer_id="fb98a600-2778-11f1-9cdc-43ca8fc8dcc9",
    subject="headoffice.security@bankofindia.bank.in",
    claims={"firstName": "Head Office", "lastName": "BOI"},
    prefix="BOI",
    user_token="tok",
)


@pytest.fixture
def local_and_tb(monkeypatch):
    """Local hierarchy returns 104; ThingsBoard authorizes 100."""

    async def fake_branch_scope(session, prefix, scope, redis):
        return ScopedBranches(branch_node_ids=["BOI-MALDATOWN"], tb_device_ids=list(LOCAL_IDS))

    async def fake_acl(settings, token, redis):
        return TB_AUTHORIZED

    monkeypatch.setattr(scope_resolver, "branch_scope", fake_branch_scope)
    monkeypatch.setattr(scope_resolver, "authorized_device_ids", fake_acl)


@pytest.mark.asyncio
async def test_scope_never_exceeds_thingsboard(local_and_tb) -> None:
    scoped = await resolved_scope(None, None, TENANT, SETTINGS)  # type: ignore[arg-type]
    assert set(scoped.tb_device_ids) <= set(TB_AUTHORIZED), "v2 exposed devices TB forbids"
    assert not set(scoped.tb_device_ids) & EXCESS
    assert len(scoped.tb_device_ids) == 100


@pytest.mark.asyncio
async def test_chat_path_gets_the_narrowed_scope(local_and_tb) -> None:
    """The bug lived here, not in the HTTP dependency."""
    from app.query.handlers import _default_scope

    ctx = SimpleNamespace(
        tenant=TENANT, db=None, redis=None, tb=SimpleNamespace(settings=SETTINGS)
    )
    scoped = await _default_scope(ctx)  # type: ignore[arg-type]
    assert len(scoped.tb_device_ids) == 100
    assert not set(scoped.tb_device_ids) & EXCESS


@pytest.mark.asyncio
async def test_branch_name_gate_gets_the_narrowed_scope(local_and_tb) -> None:
    """The gate resolves a branch NAME to a device id; on an unnarrowed scope it
    would hand back a device ThingsBoard forbids, disagreeing with the data gate."""
    from app.query.orchestrate import _default_gate

    captured: dict[str, object] = {}

    async def fake_load_directory(db, prefix):
        return SimpleNamespace(leaves=["BOI-MALDATOWN"])

    def fake_gate_and_resolve(question, directory, scoped):
        captured["scoped"] = scoped
        return SimpleNamespace(unauthorized_branch=None, device_id=None, branch_name=None)

    import app.query.orchestrate as orch

    orig_load, orig_gate = orch.load_directory, orch.gate_and_resolve
    orch.load_directory, orch.gate_and_resolve = fake_load_directory, fake_gate_and_resolve
    try:
        ctx = SimpleNamespace(
            tenant=TENANT, db=None, redis=None, tb=SimpleNamespace(settings=SETTINGS)
        )
        await _default_gate("status of Liluah", ctx)  # type: ignore[arg-type]
    finally:
        orch.load_directory, orch.gate_and_resolve = orig_load, orig_gate

    scoped = captured["scoped"]
    assert len(scoped.tb_device_ids) == 100  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_unconfirmed_permissions_fail_closed(monkeypatch) -> None:
    """TB unreachable must refuse, never fall back to the unchecked local scope."""

    async def fake_branch_scope(session, prefix, scope, redis):
        return ScopedBranches(branch_node_ids=["b"], tb_device_ids=list(LOCAL_IDS))

    async def boom(settings, token, redis):
        raise PermissionCheckUnavailable("thingsboard unreachable")

    monkeypatch.setattr(scope_resolver, "branch_scope", fake_branch_scope)
    monkeypatch.setattr(scope_resolver, "authorized_device_ids", boom)

    with pytest.raises(PermissionCheckUnavailable):
        await resolved_scope(None, None, TENANT, SETTINGS)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_chat_refuses_rather_than_500s_when_permissions_unconfirmed() -> None:
    """An exception escaping the orchestrator would be a 500 or a broken SSE stream;
    the user must get a refusal instead."""
    from app.query.orchestrate import QueryOrchestrator

    async def boom_gate(question, ctx):
        raise PermissionCheckUnavailable("thingsboard unreachable")

    orchestrator = QueryOrchestrator(gate=boom_gate)
    answer = await orchestrator.ask("how many devices", ctx=None)  # type: ignore[arg-type]
    assert answer.structured.get("error") == "permissions_unavailable"
    assert "could not confirm" in answer.text.lower()


class _FakeClient:
    """Stands in for UserAwareThingsBoardClient with VERBATIM production response
    shapes, captured from app.swatch360.seple.in on 2026-07-27."""

    def __init__(self, user: dict, customer_page: dict, tenant_page: dict) -> None:
        self._user, self._customer_page, self._tenant_page = user, customer_page, tenant_page
        self.calls: list[str] = []

    async def current_user(self):
        self.calls.append("current_user")
        return self._user

    async def devices(self, customer_id: str, page_size: int = 100):
        self.calls.append(f"customer:{customer_id}")
        return self._customer_page

    async def tenant_devices(self, page_size: int = 100):
        self.calls.append("tenant")
        return self._tenant_page

    async def close(self) -> None:
        pass


def _install(monkeypatch, client):
    from app.auth import tb_acl

    monkeypatch.setattr(tb_acl, "UserAwareThingsBoardClient", lambda s, t: client)
    return tb_acl


class _NoCache:
    async def get(self, k):
        return None

    async def set(self, k, v, **kw):
        return None


@pytest.mark.asyncio
async def test_tenant_admin_uses_tenant_devices_not_its_null_customer(monkeypatch) -> None:
    """A tenant admin's customerId is ThingsBoard's null-UUID sentinel
    (13814000-1dd2-11b2-8080-808080808080), which is not a real customer and 404s.
    Authority must be checked FIRST — reversing the branch order would send every
    admin to a nonexistent customer and refuse them on every question.
    """
    client = _FakeClient(
        user={
            "authority": "TENANT_ADMIN",
            "email": "info@seple.in",
            "customerId": {"entityType": "CUSTOMER", "id": "13814000-1dd2-11b2-8080-808080808080"},
            "tenantId": {"entityType": "TENANT", "id": "24d74bb0-2061-11ee-86d5-f58fb189657b"},
        },
        customer_page={"data": [{"id": {"id": "should-not-be-used"}}]},
        tenant_page={"data": [{"id": {"id": f"dev-{i}"}} for i in range(148)]},
    )
    tb_acl = _install(monkeypatch, client)
    ids = await tb_acl.authorized_device_ids(SETTINGS, "admin-token", _NoCache())
    assert len(ids) == 148
    assert "tenant" in client.calls
    assert not any(c.startswith("customer:") for c in client.calls)


@pytest.mark.asyncio
async def test_customer_user_uses_the_nested_customer_id(monkeypatch) -> None:
    """customerId arrives as {"entityType": ..., "id": ...}, not a bare string."""
    client = _FakeClient(
        user={
            "authority": "CUSTOMER_USER",
            "customerId": {"entityType": "CUSTOMER", "id": "fb98a600-2778-11f1-9cdc-43ca8fc8dcc9"},
        },
        customer_page={"data": [{"id": {"id": f"dev-{i}"}} for i in range(100)]},
        tenant_page={"data": []},
    )
    tb_acl = _install(monkeypatch, client)
    ids = await tb_acl.authorized_device_ids(SETTINGS, "user-token", _NoCache())
    assert len(ids) == 100
    assert "customer:fb98a600-2778-11f1-9cdc-43ca8fc8dcc9" in client.calls


@pytest.mark.asyncio
async def test_user_with_no_customer_is_authorized_for_nothing(monkeypatch) -> None:
    client = _FakeClient(
        user={"authority": "CUSTOMER_USER", "customerId": None},
        customer_page={"data": [{"id": {"id": "x"}}]},
        tenant_page={"data": [{"id": {"id": "y"}}]},
    )
    tb_acl = _install(monkeypatch, client)
    assert await tb_acl.authorized_device_ids(SETTINGS, "tok", _NoCache()) == frozenset()


@pytest.mark.asyncio
async def test_thingsboard_failure_raises_rather_than_returning_empty(monkeypatch) -> None:
    """An empty set would look like "authorized for nothing" and silently answer
    with no data; the caller must be able to tell the difference."""
    from app.auth import tb_acl

    class Boom(_FakeClient):
        async def current_user(self):
            raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(
        tb_acl, "UserAwareThingsBoardClient", lambda s, t: Boom({}, {"data": []}, {"data": []})
    )
    with pytest.raises(PermissionCheckUnavailable):
        await tb_acl.authorized_device_ids(SETTINGS, "expired", _NoCache())


def _token(exp_offset_seconds: int) -> str:
    import base64
    import json
    import time

    def seg(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'HS512'})}.{seg({'exp': int(time.time()) + exp_offset_seconds})}.sig"


class TestExpiryHandling:
    """A dead token must be reported as 'sign in again', never as 'retry in a moment'.

    Java shipped JwtParserUtil.isExpired and never called it, and its frontend had no
    401 handling at all — so an expired session degraded silently. These pin the
    behaviour we adopted instead.
    """

    def test_is_expired_detects_a_dead_token(self) -> None:
        from app.auth.tb_acl import is_expired

        assert is_expired(_token(-60)) is True
        assert is_expired(_token(3600)) is False
        assert is_expired("Bearer " + _token(-60)) is True  # prefix tolerated

    def test_is_expired_fails_open_on_junk(self) -> None:
        """Unparseable input must not be declared expired — ThingsBoard is the judge,
        and a false 'expired' would lock out a caller holding a valid token."""
        from app.auth.tb_acl import is_expired

        for junk in ("", "not-a-jwt", "a.b", "...."):
            assert is_expired(junk) is False

    @pytest.mark.asyncio
    async def test_expired_token_never_reaches_thingsboard(self, monkeypatch) -> None:
        from app.auth import tb_acl

        def explode(settings, token):
            raise AssertionError("must not call ThingsBoard for a known-dead token")

        monkeypatch.setattr(tb_acl, "UserAwareThingsBoardClient", explode)
        with pytest.raises(tb_acl.SessionExpired):
            await tb_acl.authorized_device_ids(SETTINGS, _token(-60), _NoCache())

    @pytest.mark.asyncio
    async def test_thingsboard_401_is_session_expired_not_a_generic_outage(
        self, monkeypatch
    ) -> None:
        import httpx

        from app.auth import tb_acl

        class Rejecting(_FakeClient):
            async def current_user(self):
                request = httpx.Request("GET", "https://tb/api/auth/user")
                response = httpx.Response(401, request=request)
                raise httpx.HTTPStatusError("401", request=request, response=response)

        monkeypatch.setattr(
            tb_acl,
            "UserAwareThingsBoardClient",
            lambda s, t: Rejecting({}, {"data": []}, {"data": []}),
        )
        with pytest.raises(tb_acl.SessionExpired):
            await tb_acl.authorized_device_ids(SETTINGS, _token(3600), _NoCache())

    @pytest.mark.asyncio
    async def test_server_error_stays_a_generic_outage(self, monkeypatch) -> None:
        """A 502 IS transient — it must not tell the user to sign in again."""
        import httpx

        from app.auth import tb_acl

        class Broken(_FakeClient):
            async def current_user(self):
                request = httpx.Request("GET", "https://tb/api/auth/user")
                response = httpx.Response(502, request=request)
                raise httpx.HTTPStatusError("502", request=request, response=response)

        monkeypatch.setattr(
            tb_acl,
            "UserAwareThingsBoardClient",
            lambda s, t: Broken({}, {"data": []}, {"data": []}),
        )
        with pytest.raises(PermissionCheckUnavailable) as caught:
            await tb_acl.authorized_device_ids(SETTINGS, _token(3600), _NoCache())
        assert not isinstance(caught.value, tb_acl.SessionExpired)

    @pytest.mark.asyncio
    async def test_chat_tells_the_user_to_sign_in_rather_than_retry(self) -> None:
        from app.auth.tb_acl import SessionExpired
        from app.query.orchestrate import QueryOrchestrator

        async def dead_session(question, ctx):
            raise SessionExpired("thingsboard returned 401")

        answer = await QueryOrchestrator(gate=dead_session).ask("hi", ctx=None)  # type: ignore[arg-type]
        assert answer.structured.get("error") == "session_expired"
        assert "sign in" in answer.text.lower()
        assert "retry in a moment" not in answer.text.lower()


@pytest.mark.asyncio
async def test_no_prefix_yields_empty_scope_without_calling_thingsboard(monkeypatch) -> None:
    async def never(settings, token, redis):
        raise AssertionError("must not query TB when there is no customer mapping")

    monkeypatch.setattr(scope_resolver, "authorized_device_ids", never)
    unmapped = TenantContext(
        tenant_id="t", customer_id="c", subject="s", claims={}, prefix=None, user_token="tok"
    )
    scoped = await resolved_scope(None, None, unmapped, SETTINGS)  # type: ignore[arg-type]
    assert scoped.tb_device_ids == []
