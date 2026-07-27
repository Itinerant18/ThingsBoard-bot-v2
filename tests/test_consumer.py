import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from app.ingest import consumer
from app.ingest.consumer import already_processed, mark_processed, process_event
from app.ingest.parse import EventParse


class FakeRedis:
    def __init__(self, broken: bool = False) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.broken = broken

    def _check(self) -> None:
        if self.broken:
            raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        self._check()
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def get(self, key: str) -> str | None:
        self._check()
        return self.strings.get(key)

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self._check()
        self.hashes.setdefault(key, {}).update(mapping)

    async def expire(self, key: str, seconds: int) -> None:
        self._check()


class FakeSessionFactory:
    """process_event only passes the session through to patched helpers."""

    def __call__(self):
        @asynccontextmanager
        async def _ctx():
            yield object()

        return _ctx()


D1 = str(uuid.uuid4())


def make_event(event_id: str = "e1", customer: str | None = "BOI") -> EventParse:
    return EventParse(
        tenant_id="tt",
        device_id=D1,
        event_id=event_id,
        customer_id=customer,
        payload={"data": {"active": "true"}, "cpu": "40", "tenant_id": "tt"},
    )


async def test_idempotency_roundtrip_and_fail_open() -> None:
    redis = FakeRedis()
    event = make_event()
    assert await already_processed(redis, event) is False
    await mark_processed(redis, event)
    assert await already_processed(redis, event) is True
    broken = FakeRedis(broken=True)
    assert await already_processed(broken, event) is False  # fail-open to DB dedup
    await mark_processed(broken, event)  # must not raise


@patch.object(consumer, "_device_in_customer_hierarchy", new_callable=AsyncMock, return_value=True)
@patch.object(consumer, "write_event", new_callable=AsyncMock)
async def test_process_event_stores_and_folds(write: AsyncMock, known: AsyncMock) -> None:
    redis = FakeRedis()
    result = await process_event(FakeSessionFactory(), redis, make_event())  # type: ignore[arg-type]
    assert result == "stored"
    write.assert_awaited_once()
    state = redis.hashes[f"fleet:v1:BOI:{D1}"]
    assert state["active"] == "true"  # container folded
    assert state["cpu"] == "40"
    assert "tenant_id" not in state  # envelope never folded
    assert redis.strings["evt:seen:v1:tt:e1"] == "1"  # marked processed


@patch.object(consumer, "_device_in_customer_hierarchy", new_callable=AsyncMock, return_value=True)
@patch.object(consumer, "write_event", new_callable=AsyncMock)
async def test_process_event_duplicate_skips_db(write: AsyncMock, known: AsyncMock) -> None:
    redis = FakeRedis()
    event = make_event()
    await mark_processed(redis, event)
    assert await process_event(FakeSessionFactory(), redis, event) == "duplicate"  # type: ignore[arg-type]
    write.assert_not_awaited()


@patch.object(consumer, "_device_in_customer_hierarchy", new_callable=AsyncMock, return_value=False)
@patch.object(consumer, "write_event", new_callable=AsyncMock)
async def test_process_event_unknown_device_no_fold(write: AsyncMock, known: AsyncMock) -> None:
    # SECURITY: device not in claimed customer's hierarchy -> persisted, never folded.
    redis = FakeRedis()
    assert await process_event(FakeSessionFactory(), redis, make_event()) == "stored_no_fold"  # type: ignore[arg-type]
    write.assert_awaited_once()
    assert redis.hashes == {}


@patch.object(consumer, "write_event", new_callable=AsyncMock)
async def test_process_event_no_customer_no_fold(write: AsyncMock) -> None:
    redis = FakeRedis()
    result = await process_event(FakeSessionFactory(), redis, make_event(customer=None))  # type: ignore[arg-type]
    assert result == "stored_no_fold"
    assert redis.hashes == {}
