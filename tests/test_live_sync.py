import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, Self

from app.auth.jwt import TenantContext
from app.hierarchy.scope import ScopedBranches
from app.query.contracts import ExtractedIntent, RequestContext
from app.query.handlers import GlobalOverview
from app.tasks.live_sync import (
    DeviceRef,
    fetch_device_fields,
    load_fleet_states,
    sync_customer,
)
from app.tasks.scheduler import run_periodic


class FakeRedis:
    """Fleet-snapshot ops: string lock keys + hash state keys + pipelined hgetall."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def expire(self, key: str, seconds: int) -> None:
        pass

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.strings.pop(key, None)
            self.hashes.pop(key, None)

    def pipeline(self, transaction: bool = False) -> "_FakePipeline":
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._keys: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def hgetall(self, key: str) -> None:
        self._keys.append(key)

    async def execute(self) -> list[dict[str, str]]:
        return [dict(self._redis.hashes.get(k, {})) for k in self._keys]


class FakeTb:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()

    async def attributes(self, device_id: str, scope: str) -> Any:
        if device_id in self.fail_for:
            raise ConnectionError("tb down")
        if scope == "SERVER_SCOPE":
            return [{"key": "active", "value": "true"}]
        return []

    async def telemetry(self, device_id: str, keys: str | None = None) -> Any:
        return {"cpu": [{"ts": 1, "value": "40"}]}


D1, D2 = str(uuid.uuid4()), str(uuid.uuid4())
DEVICES = [DeviceRef(D1, "BOI-A", "BOI-A"), DeviceRef(D2, "BOI-B", "BOI-B")]


# --- scheduler ----------------------------------------------------------------


async def test_run_periodic_survives_job_errors_and_cancels() -> None:
    calls = 0

    async def job() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first run fails")

    task = asyncio.create_task(run_periodic("t", 0.005, job))
    # Wait on the condition, not a fixed sleep — timing-based waits flake on slow CI.
    for _ in range(400):
        if calls >= 2:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert calls >= 2  # error on run 1 did not kill the loop


# --- sync ----------------------------------------------------------------------


async def test_fetch_device_fields_merges_attrs_and_telemetry() -> None:
    fields = await fetch_device_fields(FakeTb(), D1)
    assert fields["active"] == "true"
    assert fields["cpu"] == "40"


async def test_sync_customer_writes_states_and_releases_lock() -> None:
    redis = FakeRedis()
    synced = await sync_customer(redis, FakeTb(), "BOI", DEVICES)
    assert synced == 2
    states = await load_fleet_states(redis, "BOI", [D1, D2])
    assert states[D1]["branch_name"] == "BOI-A"
    assert "fleet:v1:lock:BOI" not in redis.strings  # lock released


async def test_sync_customer_skips_when_locked() -> None:
    redis = FakeRedis()
    await redis.set("fleet:v1:lock:BOI", "1")
    assert await sync_customer(redis, FakeTb(), "BOI", DEVICES) == 0
    assert await load_fleet_states(redis, "BOI", [D1, D2]) == {}


async def test_sync_customer_keeps_going_after_device_failure() -> None:
    redis = FakeRedis()
    synced = await sync_customer(redis, FakeTb(fail_for={D1}), "BOI", DEVICES)
    assert synced == 1
    states = await load_fleet_states(redis, "BOI", [D1, D2])
    assert D1 not in states
    assert D2 in states


async def test_load_fleet_states_skips_empty() -> None:
    redis = FakeRedis()
    assert await load_fleet_states(redis, "BOI", [D1]) == {}  # no hash stored
    assert await load_fleet_states(redis, "BOI", []) == {}


# --- GlobalOverview consumes the snapshot ---------------------------------------


def make_ctx(redis: FakeRedis) -> RequestContext:
    tenant = TenantContext(
        tenant_id="tt", customer_id="c", subject="s", claims={}, scopes=(),
        region=None, prefix="BOI", user_token="tok",
    )
    return RequestContext(tenant=tenant, db=SimpleNamespace(), redis=redis, tb=SimpleNamespace())  # type: ignore[arg-type]


def _overview(scoped: ScopedBranches) -> GlobalOverview:
    async def scope_fn(ctx: RequestContext) -> ScopedBranches:
        return scoped

    return GlobalOverview(scope_fn=scope_fn)


async def test_global_overview_counts_online_offline_from_snapshot() -> None:
    redis = FakeRedis()
    redis.hashes[f"fleet:v1:BOI:{D1}"] = {"active": "true"}
    redis.hashes[f"fleet:v1:BOI:{D2}"] = {"active": "false"}
    handler = _overview(ScopedBranches(["BOI-A", "BOI-B"], [D1, D2]))
    answer = await handler.handle(ExtractedIntent(name="global_overview"), make_ctx(redis))
    assert "1 online" in answer.text
    assert "1 offline" in answer.text
    assert answer.structured["online"] == 1


async def test_global_overview_falls_back_without_snapshot() -> None:
    handler = _overview(ScopedBranches(["BOI-A"], [D1]))
    answer = await handler.handle(ExtractedIntent(name="global_overview"), make_ctx(FakeRedis()))
    assert "1 device(s) in your authorized scope" in answer.text
    assert "online" not in answer.text


async def test_global_overview_never_counts_out_of_scope_devices() -> None:
    # Snapshot contains ANOTHER region's device; scoped list excludes it.
    redis = FakeRedis()
    other = str(uuid.uuid4())
    redis.hashes[f"fleet:v1:BOI:{D1}"] = {"active": "true"}
    redis.hashes[f"fleet:v1:BOI:{other}"] = {"active": "true"}
    handler = _overview(ScopedBranches(["BOI-A"], [D1]))
    answer = await handler.handle(ExtractedIntent(name="global_overview"), make_ctx(redis))
    assert answer.structured["online"] == 1  # not 2