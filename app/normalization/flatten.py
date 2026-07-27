"""Expand ThingsBoard's nested container attributes into dotted aliases.

ThingsBoard returns whole subsystems as JSON objects rather than flat keys:

    gateway = {"powerStatus": "Off", "systemStatus": "Inactive", "healthStatus": ...}
    rock    = {"HddINFO": [...], "model": "...", "firmwareVersion": "..."}
    basSystemIntegration = {"basPowerStatus": {"batteryVoltage": 13.4, ...}, ...}

docs/Telimetry-Attribute-key.md addresses those fields by dotted path
("gateway.powerStatus", "basSystemIntegration.basPowerStatus.batteryVoltage"), and
there are ZERO literal dotted key names in the fleet — verified across all 128
devices — so the path has to be walked.

Expanding ONCE here, right after the raw dict is assembled, rather than teaching
every reader to resolve paths: the ladders in answer_support and snapshot.py look
keys up in a dozen places, and one missed call site returns None silently, which is
precisely the bug this fixes.
"""

import json
from collections.abc import Mapping
from typing import Any

# basSystemIntegration.basPowerStatus.batteryVoltage is the deepest path the key doc
# uses; the bound stops a pathological payload from exploding the dict.
_MAX_DEPTH = 3


def _as_container(value: Any) -> Mapping[str, Any] | None:
    """A dict, or a JSON string holding one.

    Both shapes occur: the live ThingsBoard fetch yields real dicts, while the Redis
    fleet snapshot stores every value as a string (live_sync._encode JSON-encodes
    non-strings), so the snapshot path sees '{"powerStatus": "Off"}'.
    """
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                return None
            if isinstance(parsed, dict):
                return parsed
    return None


def expand_containers(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return `raw` plus a dotted alias for every nested container field.

    Existing keys are never overwritten: a device that sends BOTH a flat
    `battery_voltage` and a nested `battery_status.battery_voltage` keeps the flat
    one, because the flat name is what the key doc treats as canonical.
    """
    out: dict[str, Any] = dict(raw)

    def walk(prefix: str, container: Mapping[str, Any], depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        for key, value in container.items():
            path = f"{prefix}.{key}"
            if path not in out:
                out[path] = value
            nested = _as_container(value)
            if nested is not None:
                walk(path, nested, depth + 1)

    for key, value in list(raw.items()):
        container = _as_container(value)
        if container is not None:
            walk(str(key), container, 1)
    return out


def request_keys(keys: list[str]) -> list[str]:
    """Map lookup keys to the key names ThingsBoard will actually accept.

    A dotted path is OUR addressing scheme, not a ThingsBoard key — asking the
    timeseries API for "gateway.powerStatus" matches nothing. Request the container
    ("gateway") and let expand_containers resolve the path afterwards.
    """
    seen: dict[str, None] = {}
    for key in keys:
        seen.setdefault(key.split(".", 1)[0], None)
    return list(seen)
