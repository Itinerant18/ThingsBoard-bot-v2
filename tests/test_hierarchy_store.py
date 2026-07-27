"""DB-level acceptance tests for hierarchy persistence (slice 1).

Uses a real Postgres via testcontainers; catches multi-device shared-path imports
(duplicate node_ids in one upsert) and verifies closure-table rebuild + idempotency.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.db.models import Base, BranchAncestorPath, HierarchyNode
from app.hierarchy.parser import ParsedNode, parse_device_path
from app.hierarchy.store import rebuild_ancestor_paths, upsert_nodes


@pytest.fixture(scope="module")
def pg_url() -> AsyncIterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture()
async def session(pg_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(pg_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def shared_path_nodes() -> list[ParsedNode]:
    """3 devices sharing 'Bank of India → ZO Kolkata → RO Howrah → <branch>'."""
    nodes: list[ParsedNode] = []
    for branch in ("BOI-MALDATOWN", "BOI-PARKST", "BOI-SALTLAKE"):
        nodes.extend(
            parse_device_path(
                "BOI",
                branch,
                str(uuid.uuid4()),
                f"Bank of India → ZO Kolkata → RO Howrah → {branch}",
            )
        )
    return nodes


async def test_shared_path_import_dedupes_and_builds_closure(session: AsyncSession) -> None:
    await upsert_nodes(session, shared_path_nodes())
    await rebuild_ancestor_paths(session, "BOI")
    await session.commit()

    rows = (await session.execute(select(HierarchyNode))).scalars().all()
    by_type: dict[str, int] = {}
    for row in rows:
        by_type[row.node_type] = by_type.get(row.node_type, 0) + 1
    assert by_type == {"HO": 1, "ZO": 1, "RO": 1, "BRANCH": 3}

    # each branch: self + RO + ZO + HO = 4 closure rows
    for branch in ("BOI-MALDATOWN", "BOI-PARKST", "BOI-SALTLAKE"):
        ancestors = (
            (
                await session.execute(
                    select(BranchAncestorPath.ancestor_id).where(
                        BranchAncestorPath.node_id == branch
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(ancestors) == sorted([branch, "BOI:RO HOWRAH", "BOI:ZO KOLKATA", "BOI_HO"])


async def test_reimport_is_idempotent(session: AsyncSession) -> None:
    for _ in range(2):
        await upsert_nodes(session, shared_path_nodes())
        await rebuild_ancestor_paths(session, "BOI")
        await session.commit()

    node_count = (await session.execute(select(func.count(HierarchyNode.node_id)))).scalar()
    path_count = (await session.execute(select(func.count()).select_from(BranchAncestorPath))).scalar()
    assert node_count == 6  # 1 HO + 1 ZO + 1 RO + 3 branches
    assert path_count == 3 * 4 + 1 + 2 + 3  # branches(4 each) + HO(1) + ZO(2) + RO(3)
