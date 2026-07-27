from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def initialize_timescale(engine: AsyncEngine) -> None:
    """Opt-in PostgreSQL/Timescale conversion; call once after migration."""
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT create_hypertable('device_event', 'time', if_not_exists => TRUE)")
        )
        await conn.execute(
            text(
                "SELECT add_retention_policy('device_event', INTERVAL '90 days', if_not_exists => TRUE)"
            )
        )
