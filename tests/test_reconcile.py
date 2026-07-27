import uuid
from datetime import datetime
from typing import Self

from app.db.models import HierarchyNode
from app.tasks.reconcile import detect_missing, reconcile_customer


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

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


class _Result:
    def __init__(self, values: list) -> None:
        self._values = values

    def scalars(self) -> list:
        return self._values


class FakeSession:
    """Returns queued results for successive execute() calls."""

    def __init__(self, results: list[list]) -> None:
        self._results = list(results)

    async def execute(self, stmt: object) -> _Result:
        return _Result(self._results.pop(0))


D1, D2 = str(uuid.uuid4()), str(uuid.uuid4())


def leaf(device_id: str, node_id: str) -> HierarchyNode:
    return HierarchyNode(
        node_id=node_id, customer_id="BOI", parent_id=None, node_type="branch",
        node_level=3, display_name=node_id, is_leaf=True, tb_device_id=uuid.UUID(device_id),
    )


def test_detect_missing() -> None:
    assert detect_missing([D1, D2], {D1, D2}, {D1}) == {D2}
    assert detect_missing([D1], {D1, "rogue"}, set()) == {D1}  # rogue never counted
    assert detect_missing([D1, D2], set(), set()) == set()  # no events -> nothing expected


async def test_reconcile_consistent_no_repair() -> None:
    redis = FakeRedis()
    redis.hashes[f"fleet:v1:BOI:{D1}"] = {"active": "true"}
    session = FakeSession([[leaf(D1, "BOI-A")], [D1]])
    calls: list[str] = []

    async def repair(s, r, customer: str, start: datetime, end: datetime) -> None:
        calls.append(customer)

    result = await reconcile_customer(session, redis, "BOI", repair_fn=repair)  # type: ignore[arg-type]
    assert result.missing == 0
    assert result.repaired is False
    assert calls == []


async def test_reconcile_drift_triggers_repair() -> None:
    # D2 has DB events but no snapshot -> drift -> repair called.
    redis = FakeRedis()
    redis.hashes[f"fleet:v1:BOI:{D1}"] = {"active": "true"}
    session = FakeSession([[leaf(D1, "BOI-A"), leaf(D2, "BOI-B")], [D1, D2]])
    calls: list[str] = []

    async def repair(s, r, customer: str, start: datetime, end: datetime) -> None:
        calls.append(customer)

    result = await reconcile_customer(session, redis, "BOI", repair_fn=repair)  # type: ignore[arg-type]
    assert result.missing == 1
    assert result.repaired is True
    assert calls == ["BOI"]


async def test_reconcile_drift_without_auto_repair() -> None:
    redis = FakeRedis()
    session = FakeSession([[leaf(D1, "BOI-A")], [D1]])
    calls: list[str] = []

    async def repair(s, r, customer: str, start: datetime, end: datetime) -> None:
        calls.append(customer)

    result = await reconcile_customer(
        session, redis, "BOI", auto_repair=False, repair_fn=repair  # type: ignore[arg-type]
    )
    assert result.missing == 1
    assert result.repaired is False
    assert calls == []
