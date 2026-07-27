"""Minimal asyncio periodic runner — v2's replacement for Spring @Scheduled.

One background task per job, started in the FastAPI lifespan and cancelled on
shutdown. A job exception is logged and the loop continues (Java parity: a failed
sync cycle never kills the scheduler)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_periodic(
    name: str,
    interval_seconds: float,
    job: Callable[[], Awaitable[None]],
    initial_delay_seconds: float = 0.0,
) -> None:
    if initial_delay_seconds > 0:
        await asyncio.sleep(initial_delay_seconds)
    while True:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[SCHEDULER] job '%s' failed; next run continues", name)
        await asyncio.sleep(interval_seconds)
