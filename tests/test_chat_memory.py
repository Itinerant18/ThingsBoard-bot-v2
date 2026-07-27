import uuid
from types import SimpleNamespace
from typing import Any

from app.auth.jwt import TenantContext
from app.query import memory
from app.query.branch_names import BranchGateResult
from app.query.contracts import Answer, ExtractedIntent, RequestContext
from app.query.orchestrate import QueryOrchestrator


class FakeRedis:
    """Just the ops memory.py uses: rpush/ltrim/expire/lrange, set/get, delete."""

    def __init__(self, broken: bool = False) -> None:
        self.lists: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}
        self.broken = broken

    def _check(self) -> None:
        if self.broken:
            raise ConnectionError("redis down")

    async def rpush(self, key: str, *values: str) -> int:
        self._check()
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> None:
        self._check()
        items = self.lists.get(key, [])
        end_idx = len(items) if end == -1 else end + 1
        self.lists[key] = items[start if start >= 0 else max(0, len(items) + start) : end_idx]

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        self._check()
        return list(self.lists.get(key, []))

    async def expire(self, key: str, seconds: int) -> None:
        self._check()

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._check()
        self.strings[key] = value

    async def get(self, key: str) -> str | None:
        self._check()
        return self.strings.get(key)

    async def delete(self, *keys: str) -> None:
        self._check()
        for key in keys:
            self.lists.pop(key, None)
            self.strings.pop(key, None)


SESSION = memory.session_key("tt", "user@x", "default")
D1 = str(uuid.uuid4())


async def test_record_and_load_roundtrip() -> None:
    redis = FakeRedis()
    await memory.record_turn(redis, SESSION, "q1", "a1")
    ctx = await memory.load_context(redis, SESSION)
    assert ctx.history == (("user", "q1"), ("assistant", "a1"))


async def test_sliding_window_keeps_last_two_pairs() -> None:
    redis = FakeRedis()
    for i in range(4):
        await memory.record_turn(redis, SESSION, f"q{i}", f"a{i}")
    ctx = await memory.load_context(redis, SESSION)
    assert len(ctx.history) == memory.MAX_HISTORY_MESSAGES
    assert ctx.history[0] == ("user", "q2")  # oldest pairs dropped
    assert ctx.history[-1] == ("assistant", "a3")


async def test_active_branch_roundtrip_and_clear() -> None:
    redis = FakeRedis()
    await memory.set_active_branch(redis, SESSION, D1, "BOI-LILUAH")
    ctx = await memory.load_context(redis, SESSION)
    assert ctx.device_id == D1
    assert ctx.branch_name == "BOI-LILUAH"
    await memory.clear(redis, SESSION)
    assert await memory.load_context(redis, SESSION) == memory.ChatContext()


async def test_memory_fails_open_when_redis_down() -> None:
    redis = FakeRedis(broken=True)
    await memory.record_turn(redis, SESSION, "q", "a")  # must not raise
    await memory.set_active_branch(redis, SESSION, D1, None)
    assert await memory.load_context(redis, SESSION) == memory.ChatContext()


async def test_corrupt_history_entries_skipped() -> None:
    redis = FakeRedis()
    redis.lists[f"chatmem:v1:{SESSION}:hist"] = ["not-json", '{"role":"user","text":"ok"}']
    ctx = await memory.load_context(redis, SESSION)
    assert ctx.history == (("user", "ok"),)


# --- orchestrator wiring ------------------------------------------------------


class _CaptureHandler:
    def __init__(self) -> None:
        self.seen: ExtractedIntent | None = None

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return True

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        self.seen = intent
        return Answer("answered")


def make_ctx(redis: FakeRedis) -> RequestContext:
    tenant = TenantContext(
        tenant_id="tt", customer_id="c", subject="user@x", claims={}, scopes=(),
        region=None, prefix="BOI", user_token="tok",
    )
    return RequestContext(tenant=tenant, db=SimpleNamespace(), redis=redis, tb=SimpleNamespace())  # type: ignore[arg-type]


def _orchestrator(gate_result: BranchGateResult, capture: _CaptureHandler) -> QueryOrchestrator:
    async def gate(question: str, ctx: Any) -> BranchGateResult:
        return gate_result

    orch = QueryOrchestrator(gate=gate)
    orch.handlers = [capture]
    return orch


async def test_follow_up_uses_remembered_branch() -> None:
    redis = FakeRedis()
    await memory.set_active_branch(redis, SESSION, D1, "BOI-LILUAH")
    capture = _CaptureHandler()
    orch = _orchestrator(BranchGateResult(), capture)  # no branch in this question
    await orch.ask("and its cctv status?", make_ctx(redis), session_id=SESSION)
    assert capture.seen is not None
    assert capture.seen.device_id == D1
    assert capture.seen.node_name == "BOI-LILUAH"


async def test_gate_resolution_updates_memory_and_records_turn() -> None:
    redis = FakeRedis()
    capture = _CaptureHandler()
    orch = _orchestrator(BranchGateResult(device_id=D1, branch_name="BOI-LILUAH"), capture)
    await orch.ask("battery voltage of Liluah", make_ctx(redis), session_id=SESSION)
    ctx = await memory.load_context(redis, SESSION)
    assert ctx.device_id == D1
    assert ctx.history == (("user", "battery voltage of Liluah"), ("assistant", "answered"))


async def test_no_session_id_means_no_memory() -> None:
    redis = FakeRedis()
    capture = _CaptureHandler()
    orch = _orchestrator(BranchGateResult(), capture)
    await orch.ask("gateway status", make_ctx(redis))
    assert redis.lists == {}
    assert redis.strings == {}
