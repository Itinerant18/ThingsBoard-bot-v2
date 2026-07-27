import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.api import webhooks
from app.config import Settings
from app.main import create_app


class FakePublisher:
    def __init__(self, broken: bool = False) -> None:
        self.published: list[tuple[bytes, str | None]] = []
        self.broken = broken

    async def publish(self, body: bytes, customer: str | None = None) -> None:
        if self.broken:
            raise ConnectionError("broker down")
        self.published.append((body, customer))


def _payload() -> dict:
    return {"tenant_id": "tt", "device_id": str(uuid.uuid4()), "event_id": "e1", "data": {"a": 1}}


def _app(publisher: FakePublisher | None, fallback: bool = True):
    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        webhook_direct_write_fallback=fallback,
    )
    app = create_app(settings)
    app.state.settings = settings
    app.state.publisher = publisher

    @asynccontextmanager
    async def _session():
        yield object()

    app.state.session_factory = _session
    return app


async def _post(app, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.post("/webhooks/thingsboard", json=body)


async def test_webhook_publishes_to_queue_with_customer_routing() -> None:
    publisher = FakePublisher()
    response = await _post(_app(publisher), {**_payload(), "customer_id": "BOI"})
    assert response.status_code == 202
    assert response.json() == {"queued": True}
    body, customer = publisher.published[0]
    assert json.loads(body)["tenant_id"] == "tt"
    assert customer == "BOI"  # routed to the customer's topic key


async def test_webhook_publishes_without_customer() -> None:
    publisher = FakePublisher()
    response = await _post(_app(publisher), _payload())
    assert response.status_code == 202
    assert publisher.published[0][1] is None


async def test_webhook_rejects_garbage_before_queue() -> None:
    publisher = FakePublisher()
    app = _app(publisher)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/webhooks/thingsboard", content=b'{"no": "required fields"}'
        )
    assert response.status_code == 422
    assert publisher.published == []  # queue never poisoned


@patch.object(webhooks, "write_event", new_callable=AsyncMock, return_value=True)
async def test_webhook_falls_back_to_db_when_broker_down(write: AsyncMock) -> None:
    response = await _post(_app(FakePublisher(broken=True), fallback=True), _payload())
    assert response.status_code == 202
    assert response.json() == {"queued": False, "accepted": True}
    write.assert_awaited_once()


async def test_webhook_503_when_broker_down_and_no_fallback() -> None:
    response = await _post(_app(FakePublisher(broken=True), fallback=False), _payload())
    assert response.status_code == 503


@patch.object(webhooks, "write_event", new_callable=AsyncMock, return_value=True)
async def test_webhook_direct_write_when_queue_disabled(write: AsyncMock) -> None:
    response = await _post(_app(publisher=None), _payload())
    assert response.status_code == 202
    assert response.json() == {"queued": False, "accepted": True}
    write.assert_awaited_once()
