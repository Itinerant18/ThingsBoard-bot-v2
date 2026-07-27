"""A null telemetry reading must never erase a populated attribute.

Measured against production: requesting keys explicitly makes ThingsBoard answer for
keys that have no timeseries at all, returning {"gateway": [{"ts": ..., "value": null}]}.
Since telemetry is applied after attributes, that null overwrote the fully populated
`gateway` container, and every subsystem read came back None:

    gateway = None   ← was {"powerStatus": "Off", ...} one line earlier
    cctv    = None
    bas     = None

The fleet-snapshot path escaped this only because it happens to request telemetry
without a key list, so ThingsBoard omits the empty keys — luck, not design, so both
sites are guarded.
"""

from typing import Any

import pytest

from app.query.handlers import _load_raw
from app.tasks.live_sync import fetch_device_fields

GATEWAY = {"powerStatus": "Off", "systemStatus": "Inactive"}


class _Client:
    """Attributes hold the real container; telemetry answers null for it."""

    def __init__(self, telemetry: dict[str, Any]) -> None:
        self._telemetry = telemetry
        self.requested: str | None = None

    async def attributes(self, device_id: str, scope: str) -> Any:
        if scope == "SERVER_SCOPE":
            return [
                {"key": "gateway", "value": GATEWAY},
                {"key": "battery_voltage", "value": 14.0},
            ]
        return []

    async def telemetry(self, device_id: str, keys: str | None = None) -> Any:
        self.requested = keys
        return self._telemetry

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_null_telemetry_does_not_erase_an_attribute() -> None:
    client = _Client({"gateway": [{"ts": 1, "value": None}]})
    raw = await _load_raw(client, "dev-1", ["gateway.powerStatus", "battery_voltage"])
    assert raw["gateway"] == GATEWAY
    assert raw["gateway.powerStatus"] == "Off"  # and the path still resolves
    assert raw["battery_voltage"] == 14.0


@pytest.mark.asyncio
async def test_a_real_telemetry_value_still_wins_over_the_attribute() -> None:
    """The guard must only suppress nulls — fresher telemetry must still override."""
    client = _Client({"battery_voltage": [{"ts": 2, "value": 12.5}]})
    raw = await _load_raw(client, "dev-1", ["battery_voltage"])
    assert raw["battery_voltage"] == 12.5


@pytest.mark.asyncio
async def test_dotted_paths_are_not_sent_to_thingsboard() -> None:
    client = _Client({})
    await _load_raw(client, "dev-1", ["gateway.powerStatus", "gateway.systemStatus"])
    assert client.requested == "gateway"  # not "gateway.powerStatus,..."


@pytest.mark.asyncio
async def test_live_sync_is_guarded_too() -> None:
    class SyncClient(_Client):
        async def attributes(self, device_id: str, scope: str) -> Any:
            return [{"key": "gateway", "value": GATEWAY}] if scope == "SERVER_SCOPE" else []

    client = SyncClient({"gateway": [{"ts": 1, "value": None}]})
    fields = await fetch_device_fields(client, "dev-1")
    assert fields["gateway"] == GATEWAY
