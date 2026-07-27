from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BranchAncestorPath, HierarchyNode
from app.hierarchy.parser import ParsedNode


async def upsert_nodes(session: AsyncSession, nodes: list[ParsedNode]) -> int:
    # Dedupe by node_id: devices sharing a path each emit the same HO/ZO/RO nodes, and
    # Postgres rejects duplicate rows in one INSERT .. ON CONFLICT DO UPDATE statement
    # ("cannot affect row a second time"). First occurrence wins; duplicates are identical.
    nodes = list({item.node_id: item for item in reversed(nodes)}.values())
    if not nodes:
        return 0
    values = [
        {
            "node_id": item.node_id,
            "customer_id": item.customer_id,
            "parent_id": item.parent_id,
            "node_type": item.node_type,
            "node_level": item.node_level,
            "display_name": item.display_name,
            "is_leaf": item.is_leaf,
            "tb_device_id": item.tb_device_id,
        }
        for item in nodes
    ]
    stmt = (
        insert(HierarchyNode)
        .values(values)
        .on_conflict_do_update(
            index_elements=[HierarchyNode.node_id],
            set_={
                "display_name": insert(HierarchyNode).excluded.display_name,
                "parent_id": insert(HierarchyNode).excluded.parent_id,
                "node_type": insert(HierarchyNode).excluded.node_type,
                "node_level": insert(HierarchyNode).excluded.node_level,
                "is_leaf": insert(HierarchyNode).excluded.is_leaf,
                "tb_device_id": insert(HierarchyNode).excluded.tb_device_id,
            },
        )
    )
    await session.execute(stmt)
    return len(nodes)


async def rebuild_ancestor_paths(session: AsyncSession, customer_id: str) -> int:
    nodes = list(
        (
            await session.execute(
                select(HierarchyNode).where(HierarchyNode.customer_id == customer_id)
            )
        ).scalars()
    )
    await session.execute(
        delete(BranchAncestorPath).where(
            BranchAncestorPath.node_id.in_([node.node_id for node in nodes])
        )
    )
    parents = {node.node_id: node.parent_id for node in nodes}
    rows: list[dict[str, object]] = []
    for node in nodes:
        current: str | None = node.node_id
        depth = 0
        while current is not None:
            rows.append({"node_id": node.node_id, "ancestor_id": current, "depth": depth})
            current = parents.get(current)
            depth += 1
    if rows:
        await session.execute(insert(BranchAncestorPath).values(rows))
    return len(rows)
