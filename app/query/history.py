"""Historical answers from the device_telemetry hypertable.

The table has been accumulating since the persistence slice — millions of rows across
the fleet — and until now nothing read it. Every chat answer was the latest value from
a live ThingsBoard call, so "battery voltage of Liluah last week" was unanswerable
even though the data was sitting in Tiger.

Only value_num is aggregated: min/avg/max are meaningless over status strings. For a
text-valued key the answer reports how often it was observed and how many DISTINCT
values it took, which is the useful question for a status field ("did it flap?").
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeviceTelemetry

logger = logging.getLogger(__name__)

# Intent -> the telemetry key holding its numeric series. Only intents whose value is
# genuinely numeric appear here; the names are the flat keys from
# docs/Telimetry-Attribute-key.md, which is what live sync and backfill both write.
NUMERIC_KEY_FOR_INTENT: dict[str, str] = {
    "battery_voltage": "battery_voltage",
    "battery_health": "battery_voltage",
    "ac_voltage": "ac_voltage",
    "system_current": "system_current",
    "connected_devices": "statusbox_no_of_connected_device",
}

# Intent -> a status key worth summarising as "how much did it change".
STATUS_KEY_FOR_INTENT: dict[str, str] = {
    "gateway_status": "gateway_sts",
    "cctv_status": "cctv_sts",
    "network_status": "statusbox_network",
    "sos_status": "statusbox_sos_status",
}


@dataclass(frozen=True)
class NumericSummary:
    key: str
    samples: int
    minimum: float
    average: float
    maximum: float
    latest: float | None


@dataclass(frozen=True)
class StatusSummary:
    key: str
    samples: int
    distinct_values: int
    latest: str | None


async def numeric_summary(
    session: AsyncSession, device_id: str, key: str, hours: int
) -> NumericSummary | None:
    """min/avg/max/latest for one key over a window. None when nothing was recorded."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    row = (
        await session.execute(
            select(
                func.count(DeviceTelemetry.value_num),
                func.min(DeviceTelemetry.value_num),
                func.avg(DeviceTelemetry.value_num),
                func.max(DeviceTelemetry.value_num),
            ).where(
                DeviceTelemetry.device_id == device_id,
                DeviceTelemetry.key == key,
                DeviceTelemetry.time >= since,
                DeviceTelemetry.value_num.is_not(None),
            )
        )
    ).one()
    samples, minimum, average, maximum = row
    if not samples:
        return None

    latest = (
        await session.execute(
            select(DeviceTelemetry.value_num)
            .where(
                DeviceTelemetry.device_id == device_id,
                DeviceTelemetry.key == key,
                DeviceTelemetry.time >= since,
                DeviceTelemetry.value_num.is_not(None),
            )
            .order_by(DeviceTelemetry.time.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return NumericSummary(
        key=key,
        samples=int(samples),
        minimum=float(minimum),
        average=round(float(average), 2),
        maximum=float(maximum),
        latest=None if latest is None else float(latest),
    )


async def status_summary(
    session: AsyncSession, device_id: str, key: str, hours: int
) -> StatusSummary | None:
    """Sample count + how many distinct values a status key took over the window."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    row = (
        await session.execute(
            select(
                func.count(DeviceTelemetry.value_text),
                func.count(func.distinct(DeviceTelemetry.value_text)),
            ).where(
                DeviceTelemetry.device_id == device_id,
                DeviceTelemetry.key == key,
                DeviceTelemetry.time >= since,
            )
        )
    ).one()
    samples, distinct = row
    if not samples:
        return None
    latest = (
        await session.execute(
            select(DeviceTelemetry.value_text)
            .where(
                DeviceTelemetry.device_id == device_id,
                DeviceTelemetry.key == key,
                DeviceTelemetry.time >= since,
            )
            .order_by(DeviceTelemetry.time.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return StatusSummary(
        key=key,
        samples=int(samples),
        distinct_values=int(distinct),
        latest=None if latest is None else str(latest),
    )
