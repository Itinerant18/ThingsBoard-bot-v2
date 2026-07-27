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
