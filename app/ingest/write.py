from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeviceEvent
from app.ingest.parse import EventParse


async def write_event(session: AsyncSession, event: EventParse) -> bool:
    stmt = (
        insert(DeviceEvent)
        .values(
            tenant_id=event.tenant_id,
            customer_id=event.customer_id,
            event_id=event.event_id,
            device_id=event.device_id,
            event_type=event.event_type,
            time=event.time,
            payload=event.payload,
        )
        # Renamed in migration 0002: TimescaleDB requires the partition column in
        # every unique index, so `time` joined the key. Naming the old constraint
        # here would raise on every insert and stop ingestion silently, because the
        # consumer logs and moves on.
        .on_conflict_do_nothing(constraint="uq_event_tenant_event_time")
    )
    result = await session.execute(stmt)
    await session.commit()
    return bool(getattr(result, "rowcount", 0))
