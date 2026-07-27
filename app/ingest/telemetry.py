"""Persist every device key to the device_telemetry hypertable.

Redis stays the hot cache (latest state, 15-minute TTL); Tiger becomes the history.
Both are written from the same fetched dict, so they cannot disagree about what a
device reported.

Volume: 128 devices x ~1150 keys every 60s is ~212M rows/day if each cycle writes
unconditionally. The default therefore writes a row only when a key's value CHANGES.
No key is excluded and no information is lost — a value is a step function between
observations, so "value at time T" is exact via the last row before T. Set
TELEMETRY_WRITE_MODE=all to store every observation instead.
"""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeviceTelemetry

logger = logging.getLogger(__name__)

# Postgres caps btree index entries; a pathological key would abort the whole batch.
_MAX_KEY_LEN = 255
# Bookkeeping written alongside device state by live_sync — not device data.
_SKIP_KEYS = frozenset({"device_id", "branch_name", "node_id"})


def _split_value(value: Any) -> tuple[float | None, str | None]:
    """(numeric, text). Containers and lists are stored as JSON in value_text.

    The text form is always populated so nothing is lossy; value_num exists purely so
    time_bucket()/avg() work without casting.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        # Before the int check: bool is an int subclass, and True would become 1.0.
        return (1.0 if value else 0.0), str(value).lower()
    if isinstance(value, int | float):
        return float(value), str(value)
    if isinstance(value, Mapping | list):
        return None, json.dumps(value, default=str)
    text = str(value)
    try:
        return float(text), text
    except (TypeError, ValueError):
        return None, text


def changed_fields(
    new: Mapping[str, Any], previous: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Keys whose value differs from the previous observation.

    Comparison is on the TEXT form, because Redis returns everything as strings while
    a live ThingsBoard fetch yields typed values — comparing raw would report every
    key as changed on every cycle and defeat the whole point.
    """
    if previous is None:
        return dict(new)
    out: dict[str, Any] = {}
    for key, value in new.items():
        if _split_value(value)[1] != _split_value(previous.get(key))[1]:
            out[key] = value
    return out


async def write_telemetry(
    session: AsyncSession,
    device_id: str,
    fields: Mapping[str, Any],
    *,
    tenant_id: str | None = None,
    customer_id: str | None = None,
    observed_at: datetime | None = None,
) -> int:
    """Insert one row per key. Returns rows written."""
    if not fields:
        return 0
    stamp = observed_at or datetime.now(UTC)

    rows = []
    for key, value in fields.items():
        name = str(key)
        if name in _SKIP_KEYS or len(name) > _MAX_KEY_LEN:
            continue
        value_num, value_text = _split_value(value)
        if value_num is None and value_text is None:
            continue  # a null observation carries nothing worth a row
        rows.append(
            {
                "time": stamp,
                "device_id": device_id,
                "key": name,
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "value_num": value_num,
                "value_text": value_text,
            }
        )
    if not rows:
        return 0

    # Two sources (live sync and the webhook consumer) can observe one device in the
    # same instant; the later one is a duplicate, not an error.
    stmt = insert(DeviceTelemetry).values(rows).on_conflict_do_nothing(
        constraint="uq_telemetry_device_key_time"
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
