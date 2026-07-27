"""httpx ASGI tests for device access control (DB via testcontainers)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.auth.jwt import TenantContext
from app.clients.thingsboard import UserAwareThingsBoardClient
from app.config import Settings
from app.db.models import Base, HierarchyNode
from app.deps import current_tenant, scoped_branches, user_tb_client
from app.hierarchy.parser import ParsedNode, parse_device_path
from app.hierarchy.scope import RegionalScope, ScopedBranches
from app.hierarchy.store import rebuild_ancestor_paths, upsert_nodes
from app.main import create_app


@pytest.fixture(scope="module")
def pg_url() -> str:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture()
async def session(pg_url: str) -> AsyncSession:
    engine = create_async_engine(pg_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def build_boi_hierarchy_nodes() -> list[ParsedNode]:
    """BOI hierarchy = HO -> ZO KOLKATA -> RO HOWRAH -> b1,b2 ; ZO DELHI -> b3."""
    nodes: list[ParsedNode] = []
    branches = [
        ("BOI-MALDATOWN", "Bank of India → ZO Kolkata → RO Howrah → BOI-MALDATOWN"),
        ("BOI-PARKST", "Bank of India → ZO Kolkata → RO Howrah → BOI-PARKST"),
        ("BOI-SALTLAKE", "Bank of India → ZO Kolkata → RO Howrah → BOI-SALTLAKE"),
        ("BOI-CONNAUGHT", "Bank of India → ZO Delhi → BOI-CONNAUGHT"),
    ]
    for branch, path in branches:
        nodes.extend(parse_device_path("BOI", branch, str(uuid.uuid4()), path))
    return nodes


async def seed_hierarchy(session: AsyncSession) -> tuple[str, list[str]]:
    """Seed BOI hierarchy and return (customer_prefix, device_ids)."""
    nodes = build_boi_hierarchy_nodes()
    await upsert_nodes(session, nodes)
    await rebuild_ancestor_paths(session, "BOI")
    await session.commit()

    # Get all leaf device IDs
    result = await session.execute(
        select(HierarchyNode.tb_device_id).where(
            HierarchyNode.customer_id == "BOI", HierarchyNode.is_leaf.is_(True)
        )
    )
    device_ids = [str(row[0]) for row in result.all()]
    return "BOI", device_ids


@pytest.fixture()
async def seeded_hierarchy(session: AsyncSession) -> tuple[str, list[str]]:
    return await seed_hierarchy(session)


def make_tenant_context(
    tenant_id: str = "test-tenant",
    customer_id: str = "tb-cust-boi",
    subject: str = "user@zo.kolkata.boi.in",
    claims: dict | None = None,
    region: str = "ZO KOLKATA",
    prefix: str = "BOI",
) -> TenantContext:
    """Create a TenantContext for testing."""
    if claims is None:
        claims = {"firstName": region, "customerTitle": "Bank of India"}
    return TenantContext(
        tenant_id=tenant_id,
        customer_id=customer_id,
        subject=subject,
        claims=claims,
        scopes=(),
        region=RegionalScope(name=region, explicit=True),
        prefix=prefix,
        user_token="test-user-token",
    )


def override_dependency(value):
    """Create a dependency override that returns a fixed value."""
    async def _override():
        return value
    return _override


def auth_header(token: str = "test-token") -> dict:
    """Create Authorization header."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def app(pg_url: str):
    """Create app with test settings and initialized state."""
    test_settings = Settings(
        database_url=pg_url,
        redis_url="redis://localhost:6379/0",
        tb_url="http://localhost:8080",
        tb_user="admin",
        tb_password="password",
        jwt_signing_key="test-secret",
        strict_customer_mapping=True,
        customer_prefixes="BOI",
        customers_title_mappings="Bank of India=BOI",
    )
    
    test_app = create_app(test_settings)
    
    # Manually run the lifespan startup
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.cache.redis import create_redis
    from app.clients.thingsboard import ThingsBoardClient
    from app.db.session import build_engine
    from app.query.orchestrate import QueryOrchestrator
    
    engine = build_engine(test_settings)
    test_app.state.settings = test_settings
    test_app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    test_app.state.redis = await create_redis(test_settings.redis_url)
    test_app.state.tb = ThingsBoardClient(test_settings)
    test_app.state.orchestrator = QueryOrchestrator()
    
    yield test_app
    
    # Cleanup
    await test_app.state.tb.close()
    await test_app.state.redis.aclose()
    await engine.dispose()


@pytest.fixture()
async def client(app) -> AsyncClient:
    """Create ASGI client with initialized app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestDeviceAccess:
    """Tests for /device/{id}/telemetry and attributes endpoints."""

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.telemetry", new_callable=AsyncMock)
    async def test_T1_device_inside_scope_returns_200(
        self, mock_telemetry: AsyncMock, client: AsyncClient, seeded_hierarchy: tuple[str, list[str]], app
    ) -> None:
        """T1: device inside caller scope -> 200 (TB client monkeypatched)."""
        _, device_ids = seeded_hierarchy
        mock_telemetry.return_value = {"temperature": [{"ts": 1, "value": "25"}]}

        tenant = make_tenant_context()
        # Create scoped branches for ZO KOLKATA (first 3 devices)
        scoped = ScopedBranches(
            branch_node_ids=["BOI-MALDATOWN", "BOI-PARKST", "BOI-SALTLAKE"],
            tb_device_ids=device_ids[:3],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.telemetry = mock_telemetry

        # Override dependencies
        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            response = await client.get(f"/device/{device_ids[0]}/telemetry", headers=auth_header())
            assert response.status_code == 200
            mock_telemetry.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.telemetry", new_callable=AsyncMock)
    async def test_T2_device_outside_scope_returns_403_before_tb_call(
        self, mock_telemetry: AsyncMock, client: AsyncClient, seeded_hierarchy: tuple[str, list[str]], app
    ) -> None:
        """T2: device outside caller's region scope -> 403 BEFORE any TB call."""
        _, device_ids = seeded_hierarchy

        tenant = make_tenant_context(
            subject="user@zo.delhi.boi.in",
            claims={"firstName": "ZO DELHI", "customerTitle": "Bank of India"},
            region="ZO DELHI",
        )
        # ZO DELHI scope only includes device_ids[3]
        scoped = ScopedBranches(
            branch_node_ids=["BOI-CONNAUGHT"],
            tb_device_ids=device_ids[3:],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.telemetry = mock_telemetry

        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            # Try to access a ZO KOLKATA device with ZO DELHI token
            response = await client.get(f"/device/{device_ids[0]}/telemetry", headers=auth_header())
            assert response.status_code == 403
            mock_telemetry.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.telemetry", new_callable=AsyncMock)
    async def test_T3_device_unknown_to_hierarchy_returns_403_strict_mode(
        self, mock_telemetry: AsyncMock, client: AsyncClient, app
    ) -> None:
        """T3: device unknown to hierarchy -> 403 when STRICT_CUSTOMER_MAPPING=true."""
        tenant = make_tenant_context()
        scoped = ScopedBranches(
            branch_node_ids=["BOI-MALDATOWN", "BOI-PARKST", "BOI-SALTLAKE", "BOI-CONNAUGHT"],
            tb_device_ids=["known-device-1", "known-device-2", "known-device-3", "known-device-4"],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.telemetry = mock_telemetry

        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            unknown_device_id = str(uuid.uuid4())
            response = await client.get(f"/device/{unknown_device_id}/telemetry", headers=auth_header())
            assert response.status_code == 403
            mock_telemetry.assert_not_called()
        finally:
            app.dependency_overrides.clear()


class TestClientAttributes:
    """Tests for /device/{id}/attributes/client endpoint."""

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.attributes", new_callable=AsyncMock)
    async def test_client_attributes_inside_scope_200(
        self, mock_attrs: AsyncMock, client: AsyncClient, seeded_hierarchy: tuple[str, list[str]], app
    ) -> None:
        _, device_ids = seeded_hierarchy
        mock_attrs.return_value = {"client": {"firmware": "1.0"}}

        tenant = make_tenant_context()
        scoped = ScopedBranches(
            branch_node_ids=["BOI-MALDATOWN", "BOI-PARKST", "BOI-SALTLAKE"],
            tb_device_ids=device_ids[:3],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.attributes = mock_attrs

        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            response = await client.get(f"/device/{device_ids[0]}/attributes/client", headers=auth_header())
            assert response.status_code == 200
            mock_attrs.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.attributes", new_callable=AsyncMock)
    async def test_client_attributes_outside_scope_403(
        self, mock_attrs: AsyncMock, client: AsyncClient, seeded_hierarchy: tuple[str, list[str]], app
    ) -> None:
        _, device_ids = seeded_hierarchy

        tenant = make_tenant_context(
            subject="user@zo.delhi.boi.in",
            claims={"firstName": "ZO DELHI", "customerTitle": "Bank of India"},
            region="ZO DELHI",
        )
        scoped = ScopedBranches(
            branch_node_ids=["BOI-CONNAUGHT"],
            tb_device_ids=device_ids[3:],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.attributes = mock_attrs

        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            response = await client.get(f"/device/{device_ids[0]}/attributes/client", headers=auth_header())
            assert response.status_code == 403
            mock_attrs.assert_not_called()
        finally:
            app.dependency_overrides.clear()


class TestServerAttributes:
    """Tests for /device/{id}/attributes/server endpoint."""

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.attributes", new_callable=AsyncMock)
    async def test_server_attributes_inside_scope_200(
        self, mock_attrs: AsyncMock, client: AsyncClient, seeded_hierarchy: tuple[str, list[str]], app
    ) -> None:
        _, device_ids = seeded_hierarchy
        mock_attrs.return_value = {"server": {"config": "value"}}

        tenant = make_tenant_context()
        scoped = ScopedBranches(
            branch_node_ids=["BOI-MALDATOWN", "BOI-PARKST", "BOI-SALTLAKE"],
            tb_device_ids=device_ids[:3],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.attributes = mock_attrs

        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            response = await client.get(f"/device/{device_ids[0]}/attributes/server", headers=auth_header())
            assert response.status_code == 200
            mock_attrs.assert_called_once()
        finally:
            app.dependency_overrides.clear()


class TestDataEndpoints:
    """Tests for /api/v1/data and /all-devices endpoints."""

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.devices", new_callable=AsyncMock)
    async def test_data_endpoint_returns_scoped_branches(
        self, mock_devices: AsyncMock, client: AsyncClient, seeded_hierarchy: tuple[str, list[str]], app
    ) -> None:
        _, device_ids = seeded_hierarchy

        tenant = make_tenant_context()
        scoped = ScopedBranches(
            branch_node_ids=["BOI-MALDATOWN", "BOI-PARKST", "BOI-SALTLAKE"],
            tb_device_ids=device_ids[:3],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.devices = mock_devices

        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            mock_devices.return_value = [{"id": {"id": d}, "name": f"Branch {d}"} for d in device_ids[:3]]
            response = await client.get("/api/v1/data", headers=auth_header())
            assert response.status_code == 200
            data = response.json()
            assert "devices" in data
            # Should only return devices in ZO KOLKATA scope (3 devices)
            assert len(data["devices"]) == 3
        finally:
            app.dependency_overrides.clear()

    @patch("app.clients.thingsboard.UserAwareThingsBoardClient.devices", new_callable=AsyncMock)
    async def test_all_devices_endpoint_returns_scoped_branches(
        self, mock_devices: AsyncMock, client: AsyncClient, seeded_hierarchy: tuple[str, list[str]], app
    ) -> None:
        _, device_ids = seeded_hierarchy

        tenant = make_tenant_context(
            subject="user@zo.delhi.boi.in",
            claims={"firstName": "ZO DELHI", "customerTitle": "Bank of India"},
            region="ZO DELHI",
        )
        scoped = ScopedBranches(
            branch_node_ids=["BOI-CONNAUGHT"],
            tb_device_ids=device_ids[3:],
        )
        mock_tb_client = AsyncMock(spec=UserAwareThingsBoardClient)
        mock_tb_client.devices = mock_devices

        app.dependency_overrides[current_tenant] = override_dependency(tenant)
        app.dependency_overrides[scoped_branches] = override_dependency(scoped)
        app.dependency_overrides[user_tb_client] = override_dependency(mock_tb_client)

        try:
            mock_devices.return_value = [{"id": {"id": d}, "name": f"Branch {d}"} for d in device_ids[3:]]
            response = await client.get("/all-devices", headers=auth_header())
            assert response.status_code == 200
            data = response.json()
            assert "data" in data
            # Should only return ZO DELHI devices (1 device)
            assert len(data["data"]) == 1
        finally:
            app.dependency_overrides.clear()