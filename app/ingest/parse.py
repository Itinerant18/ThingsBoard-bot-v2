import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

# ThingsBoard rule chains post their own shape, not v2's. The live "Transform Attr Node"
# emits {deviceName, deviceId, data:{currentAttr:{...}}} — no tenant_id, no snake_case
# device_id, no ts. Normalizing here (rather than editing production rule chains) keeps
# every future chain working without a ThingsBoard-side change.
_DEVICE_ID_ALIASES = ("device_id", "deviceId", "originatorId", "entityId")
_CUSTOMER_ALIASES = ("customer_id", "customerId")
_TENANT_ALIASES = ("tenant_id", "tenantId")
_EVENT_ID_ALIASES = ("event_id", "id", "tbMessageId", "msgId")
_TS_ALIASES = ("ts", "timestamp", "eventTime")
# Containers whose contents are the actual device fields.
_VALUE_CONTAINERS = ("data", "values", "telemetry", "attributes", "currentAttr")


def _first(body: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = body.get(key)
        if value is not None and str(value).strip() not in ("", "null", "None"):
            return str(value)
    return None


def _parse_ts(value: Any) -> datetime | None:
    """Accept epoch millis (TB default) or an ISO-8601 string."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        ms = int(text)
        # Heuristic: 10-digit values are seconds, 13-digit are milliseconds.
        return datetime.fromtimestamp(ms / (1000 if ms > 10_000_000_000 else 1), UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def flatten_device_fields(body: dict[str, Any]) -> dict[str, Any]:
    """Lift nested value containers up to top-level keys.

    TB sends {"data": {"currentAttr": {"cpu": 41}}}; the normalization ladders read
    flat keys like "cpu", so unwrap one or two levels of known containers.
    """
    flat: dict[str, Any] = {}
    for container in _VALUE_CONTAINERS:
        inner = body.get(container)
        if not isinstance(inner, dict):
            continue
        for key, value in inner.items():
            if key in _VALUE_CONTAINERS and isinstance(value, dict):
                flat.update({str(k): v for k, v in value.items()})  # data.currentAttr.*
            else:
                flat[str(key)] = value
    return flat


class EventParse(BaseModel):
    tenant_id: str
    device_id: str
    event_id: str
    event_type: str = "telemetry"
    customer_id: str | None = None
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_payload(cls, body: dict[str, Any], default_tenant_id: str = "") -> "EventParse":
        device_id = _first(body, _DEVICE_ID_ALIASES)
        if not device_id:
            raise ValueError("event payload has no device id")
        # ThingsBoard never sends a tenant; fall back to the configured one so the
        # (tenant_id, event_id) dedup key and tenant-scoped alarm queries still work.
        tenant_id = _first(body, _TENANT_ALIASES) or default_tenant_id
        if not tenant_id:
            raise ValueError("event payload has no tenant id and no default configured")

        # Events without an id get a deterministic content hash; a literal "" would
        # collide on the (tenant_id, event_id) unique constraint and every id-less
        # event after the first would be silently dropped by the upsert.
        event_id = _first(body, _EVENT_ID_ALIASES)
        if not event_id:
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, default=str).encode()
            ).hexdigest()
            event_id = f"sha256:{digest}"

        return cls(
            tenant_id=tenant_id,
            device_id=device_id,
            event_id=event_id,
            event_type=str(body.get("event_type") or body.get("logType") or "telemetry"),
            customer_id=_first(body, _CUSTOMER_ALIASES),
            time=_parse_ts(_first(body, _TS_ALIASES)) or datetime.now(UTC),
            payload=body,
        )
