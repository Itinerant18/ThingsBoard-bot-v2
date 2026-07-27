import uuid
from datetime import UTC, datetime

import pytest

from app.db.models import DeviceEvent, HierarchyNode
from app.tasks.replay import ReplayInProgressError, fold_payload, replay_events


class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    async def expire(self, key: str, seconds: int) -> None:
        pass

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.strings.pop(key, None)
            self.hashes.pop(key, None)


D1 = str(uuid.uuid4())


def leaf(device_id: str | None = D1) -> HierarchyNode:
    return HierarchyNode(
        node_id="BOI-LILUAH", customer_id="BOI", parent_id=None, node_type="branch",
        node_level=3, display_name="BOI-LILUAH", is_leaf=True,
        tb_device_id=uuid.UUID(device_id) if device_id else None,
    )


def event(device_id: str, payload: dict, ts: int = 0) -> DeviceEvent:
    return DeviceEvent(
        tenant_id="tt", customer_id="BOI", event_id=f"e{ts}", device_id=device_id,
        event_type="telemetry", time=datetime.fromtimestamp(ts, UTC), payload=payload,
    )


def test_fold_payload_containers_and_envelope() -> None:
    state: dict = {}
    fold_payload(state, {"data": {"cpu": 40}, "battery": "48", "tenant_id": "x", "ts": 1})
    assert state == {"cpu": 40, "battery": "48"}  # envelope keys never folded


def test_fold_payload_newer_wins() -> None:
    state: dict = {"cpu": 10}
    fold_payload(state, {"data": {"cpu": 55}})
    assert state["cpu"] == 55


async def test_replay_folds_events_in_order_and_stores() -> None:
    redis = FakeRedis()
    events = [
        event(D1, {"data": {"active": "false"}}, ts=1),
        event(D1, {"data": {"active": "true"}, "cpu": "40"}, ts=2),
    ]
    result = await replay_events(redis, "BOI", [leaf()], events)
    assert result.devices == 1
    assert result.events == 2
    stored = redis.hashes[f"fleet:v1:BOI:{D1}"]
    assert stored["active"] == "true"  # later event won
    assert stored["cpu"] == "40"
    assert stored["branch_name"] == "BOI-LILUAH"
    assert "fleet:v1:lock:BOI" not in redis.strings  # lock released


async def test_replay_skips_devices_not_in_hierarchy() -> None:
    # SECURITY: event device_id is client-supplied; unknown devices must not be folded.
    redis = FakeRedis()
    rogue = str(uuid.uuid4())
    result = await replay_events(redis, "BOI", [leaf()], [event(rogue, {"data": {"a": 1}})])
    assert result.devices == 0
    assert result.skipped_unknown_devices == 1
    assert redis.hashes == {}  # nothing folded


async def test_replay_conflict_when_lock_held() -> None:
    redis = FakeRedis()
    redis.strings["fleet:v1:lock:BOI"] = "1"
    with pytest.raises(ReplayInProgressError):
        await replay_events(redis, "BOI", [leaf()], [event(D1, {"data": {"a": 1}})])
    assert redis.strings == {"fleet:v1:lock:BOI": "1"}  # nothing written, foreign lock kept
