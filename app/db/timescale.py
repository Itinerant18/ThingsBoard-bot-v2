import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def initialize_timescale(engine: AsyncEngine) -> None:
    """Opt-in Timescale conversion; call once after migration.

    Best-effort: this is a storage optimization, so a failure is logged and the app
    still boots on a plain Postgres table. Notably create_hypertable() rejects a table
    whose unique indexes exclude the partition column — device_event is keyed on id
    with a unique (tenant_id, event_id) for idempotency, so converting it would require
    folding `time` into both keys. Until that migration exists this call is expected to
    fail on a fresh schema, and must not take the process down with it.
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT create_hypertable('device_event', 'time', if_not_exists => TRUE)")
            )
            await conn.execute(
                text(
                    "SELECT add_retention_policy('device_event', INTERVAL '90 days', "
                    "if_not_exists => TRUE)"
                )
            )
        logger.info("[TIMESCALE] device_event hypertable ready")
    except Exception:
        logger.warning(
            "[TIMESCALE] hypertable conversion skipped; continuing on a plain table",
            exc_info=True,
        )
