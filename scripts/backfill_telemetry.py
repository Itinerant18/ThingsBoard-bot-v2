"""Backfill device_telemetry from ThingsBoard history.

Live sync only records values from the moment it starts, and device_event covered
just 19 of 128 devices, so the other 109 have no history at all. This pulls what
ThingsBoard still retains and writes it into the hypertable.

ThingsBoard rate-limits this exact access pattern: the earlier fleet extraction hit
429s above concurrency 2, so the same backoff and concurrency cap are used here.

    python -m scripts.backfill_telemetry --days 7
    python -m scripts.backfill_telemetry --days 30 --device <uuid>   # one device
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.clients.thingsboard import ThingsBoardClient
from app.config import get_settings
from app.db.models import DeviceTelemetry, HierarchyNode
from app.db.session import build_session_factory
from app.ingest.telemetry import write_telemetry

logger = logging.getLogger("backfill")

CONCURRENCY = 2  # above this ThingsBoard returns 429 for this pattern
MAX_ATTEMPTS = 5


# ThingsBoard rejects a query whose keys x limit implies too many points: 40 keys at
# limit=10000 returns 400, as does limit=50000 on its own. Small batches also mean a
# rejected batch costs one slice of one device rather than the whole device.
KEY_BATCH = 10
POINT_LIMIT = 2000


async def _get_with_retry(tb: ThingsBoardClient, path: str, params: dict[str, Any]) -> Any:
    """GET with 429-aware backoff, honouring Retry-After."""
    delay = 2.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await tb._get(path, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt == MAX_ATTEMPTS:
                raise
            wait = float(exc.response.headers.get("Retry-After") or delay)
            logger.warning("429 on %s; retrying in %.1fs", path, wait)
            await asyncio.sleep(wait)
            delay *= 2
    return None


async def _history(tb: ThingsBoardClient, device_id: str, start_ms: int, end_ms: int) -> Any:
    """Historical timeseries for every key the device declares.

    ThingsBoard returns {} for a keyless historical query, so the key list must be
    fetched first and passed explicitly — the original version omitted it and
    silently backfilled nothing.

    NOTE: only TIMESERIES keys have history. Most fleet state arrives as ATTRIBUTES,
    which ThingsBoard stores as current-value-only, so there is nothing to recover
    for them — device_telemetry is the only place that history will ever exist.
    """
    base = f"/api/plugins/telemetry/DEVICE/{device_id}"
    keys = await _get_with_retry(tb, f"{base}/keys/timeseries", {})
    if not isinstance(keys, list) or not keys:
        return {}

    merged: dict[str, Any] = {}
    for start in range(0, len(keys), KEY_BATCH):
        batch = [str(k) for k in keys[start : start + KEY_BATCH]]
        try:
            got = await _get_with_retry(
                tb,
                f"{base}/values/timeseries",
                {
                    "keys": ",".join(batch),
                    "startTs": start_ms,
                    "endTs": end_ms,
                    "limit": POINT_LIMIT,
                },
            )
        except httpx.HTTPStatusError as exc:
            # One awkward key must not cost the whole device's history.
            logger.warning(
                "batch rejected (%s) for %s: %s", exc.response.status_code, device_id, batch[:3]
            )
            continue
        if isinstance(got, dict):
            merged.update(got)
    return merged


async def backfill_device(
    tb: ThingsBoardClient, sessions: Any, device_id: str, customer: str, days: int
) -> int:
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    series = await _history(
        tb, device_id, int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    )
    if not isinstance(series, dict):
        return 0

    # Group by timestamp: every key sampled at the same instant becomes one row set,
    # matching how live sync writes an observation.
    by_time: dict[int, dict[str, Any]] = {}
    for key, points in series.items():
        if not isinstance(points, list):
            continue
        for point in points:
            if isinstance(point, dict) and "ts" in point:
                by_time.setdefault(int(point["ts"]), {})[str(key)] = point.get("value")

    written = 0
    async with sessions() as session:
        for ts, fields in sorted(by_time.items()):
            written += await write_telemetry(
                session,
                device_id,
                fields,
                customer_id=customer,
                observed_at=datetime.fromtimestamp(ts / 1000, UTC),
            )
    return written


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="how far back to pull")
    parser.add_argument("--device", default=None, help="single device uuid")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip devices that already have rows — use to resume an interrupted run",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    sessions = build_session_factory(settings)
    tb = ThingsBoardClient(settings)

    async with sessions() as session:
        rows = (
            await session.execute(
                select(HierarchyNode.tb_device_id, HierarchyNode.customer_id).where(
                    HierarchyNode.is_leaf.is_(True), HierarchyNode.tb_device_id.is_not(None)
                )
            )
        ).all()
    targets = [(str(d), c) for d, c in rows if not args.device or str(d) == args.device]

    if args.skip_existing:
        # Resume path. A killed run (a deploy recreates the container) leaves its rows
        # in Tiger, so refetching those devices is hours of pointless ThingsBoard load.
        async with sessions() as session:
            done = {
                str(r[0])
                for r in (
                    await session.execute(
                        select(DeviceTelemetry.device_id).distinct()  # type: ignore[arg-type]
                    )
                ).all()
            }
        before = len(targets)
        targets = [(d, c) for d, c in targets if d not in done]
        logger.info("skipping %d device(s) that already have history", before - len(targets))

    logger.info("backfilling %d device(s), %d day(s)", len(targets), args.days)

    semaphore = asyncio.Semaphore(CONCURRENCY)
    total = 0

    async def run(device_id: str, customer: str) -> None:
        nonlocal total
        async with semaphore:
            try:
                count = await backfill_device(tb, sessions, device_id, customer, args.days)
                total += count
                logger.info("%s -> %d rows", device_id, count)
            except Exception:
                logger.warning("backfill failed for %s", device_id, exc_info=True)

    await asyncio.gather(*(run(d, c) for d, c in targets))
    await tb.close()
    logger.info("backfill complete: %d rows", total)


if __name__ == "__main__":
    asyncio.run(main())
