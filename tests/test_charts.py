import uuid
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from app.auth.jwt import TenantContext
from app.clients.thingsboard import UserAwareThingsBoardClient
from app.config import Settings
from app.deps import current_tenant, scoped_branches, user_tb_client
from app.hierarchy.scope import RegionalScope, ScopedBranches
from app.main import create_app
from app.query.charts import chart_from_history

D1 = str(uuid.uuid4())


def test_chart_from_history_sorts_and_skips_malformed() -> None:
    history = {
        "battery": [
            {"ts": 2000, "value": 48},
            {"ts": 1000, "value": 47},
            {"value": "no-ts"},
            {"ts": "bad", "value": 1},
            "not-a-dict",
        ]
    }
    chart = chart_from_history("battery", history)
    assert chart["label"] == "battery"
    assert chart["points"] == [{"t": 1000, "y": "47"}, {"t": 2000, "y": "48"}]


def test_chart_from_history_empty_cases() -> None:
    assert chart_from_history("k", {})["points"] == []
    assert chart_from_history("k", None)["points"] == []
    assert chart_from_history("k", {"other": []})["points"] == []


# --- endpoint (dependency overrides; no DB, no lifespan) -----------------------


def _app_and_mocks(telemetry_mock: AsyncMock):
    app = create_app(
        Settings(database_url="postgresql+asyncpg://unused/unused", jwt_signing_key="t")
    )
    tenant = TenantContext(
        tenant_id="tt", customer_id="c", subject="s", claims={}, scopes=(),
        region=RegionalScope(name=None, explicit=False), prefix="BOI", user_token="tok",
    )
    tb = AsyncMock(spec=UserAwareThingsBoardClient)
    tb.telemetry = telemetry_mock

    async def _tenant() -> TenantContext:
        return tenant

    async def _scoped() -> ScopedBranches:
        return ScopedBranches(branch_node_ids=["BOI-A"], tb_device_ids=[D1])

    async def _tb() -> AsyncMock:
        return tb

    app.dependency_overrides[current_tenant] = _tenant
    app.dependency_overrides[scoped_branches] = _scoped
    app.dependency_overrides[user_tb_client] = _tb
    return app


async def test_chart_endpoint_in_scope_returns_sorted_points() -> None:
    telemetry = AsyncMock(return_value={"battery": [{"ts": 2, "value": "b"}, {"ts": 1, "value": "a"}]})
    app = _app_and_mocks(telemetry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get(f"/device/{D1}/chart", params={"key": "battery"})
    assert response.status_code == 200
    assert response.json()["points"] == [{"t": 1, "y": "a"}, {"t": 2, "y": "b"}]
    kwargs = telemetry.call_args.kwargs
    assert kwargs["keys"] == "battery"
    assert kwargs["end_ts"] - kwargs["start_ts"] == 24 * 3_600_000


async def test_chart_endpoint_out_of_scope_403_before_tb() -> None:
    telemetry = AsyncMock()
    app = _app_and_mocks(telemetry)
    other = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get(f"/device/{other}/chart", params={"key": "battery"})
    assert response.status_code == 403
    telemetry.assert_not_called()


async def test_chart_endpoint_rejects_multi_key() -> None:
    app = _app_and_mocks(AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get(f"/device/{D1}/chart", params={"key": "a,b"})
    assert response.status_code == 400


async def test_chart_endpoint_degrades_to_empty_on_tb_error() -> None:
    app = _app_and_mocks(AsyncMock(side_effect=ConnectionError("tb down")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get(f"/device/{D1}/chart", params={"key": "battery"})
    assert response.status_code == 200
    assert response.json() == {"label": "battery", "points": []}
