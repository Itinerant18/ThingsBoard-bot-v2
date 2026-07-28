from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.auth.jwt import TenantContext
from app.hierarchy.scope import ScopedBranches
from app.query.contracts import ExtractedIntent, RequestContext
from app.query.handlers import AlarmDetail


class FakeAlarmClient:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.closed = False

    async def alarms(self, device_id: str) -> dict[str, Any]:
        return {"data": self.rows.get(device_id, [])}

    async def close(self) -> None:
        self.closed = True


def _ctx() -> RequestContext:
    tenant = TenantContext(
        tenant_id="tenant",
        customer_id="customer",
        subject="user",
        claims={},
        prefix="BOI",
        user_token="user-token",
    )
    return RequestContext(
        tenant=tenant,
        db=SimpleNamespace(),  # type: ignore[arg-type]
        redis=SimpleNamespace(),  # type: ignore[arg-type]
        tb=SimpleNamespace(settings=SimpleNamespace()),  # type: ignore[arg-type]
    )


async def test_alarm_handler_reads_only_scoped_device_alarms() -> None:
    allowed = str(uuid4())
    outside = str(uuid4())
    rows = {
        allowed: [
            {
                "id": {"id": "a1"},
                "type": "BOI Camera Tamper",
                "severity": "WARNING",
                "originatorName": "BOI-DOBSON",
                "createdTime": 1784890000000,
                "status": "ACTIVE_UNACK",
            }
        ],
        outside: [
            {
                "id": {"id": "secret"},
                "type": "Outside alarm",
                "createdTime": 1784890000000,
                "status": "ACTIVE_UNACK",
            }
        ],
    }
    client = FakeAlarmClient(rows)

    async def scope_fn(ctx: RequestContext) -> ScopedBranches:
        return ScopedBranches(["BOI-DOBSON"], [allowed])

    handler = AlarmDetail(scope_fn=scope_fn, client_factory=lambda settings, token: client)
    answer = await handler.handle(
        ExtractedIntent(
            name="alarm_detail",
            raw_question="Is there a camera tamper alarm currently active?",
        ),
        _ctx(),
    )

    assert "BOI-DOBSON" in answer.text
    assert "Outside alarm" not in answer.text
    assert client.closed is True


async def test_alarm_handler_rejects_branch_outside_scope_before_fetch() -> None:
    allowed = str(uuid4())
    client = FakeAlarmClient({})

    async def scope_fn(ctx: RequestContext) -> ScopedBranches:
        return ScopedBranches(["BOI-DOBSON"], [allowed])

    handler = AlarmDetail(scope_fn=scope_fn, client_factory=lambda settings, token: client)
    answer = await handler.handle(
        ExtractedIntent(
            name="alarm_detail",
            device_id=str(uuid4()),
            raw_question="Are there alarms at that branch?",
        ),
        _ctx(),
    )

    assert answer.text == "That device is not in your authorized scope."
    assert client.closed is False
