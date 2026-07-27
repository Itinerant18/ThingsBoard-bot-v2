import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class EventParse(BaseModel):
    tenant_id: str
    device_id: str
    event_id: str
    event_type: str = "telemetry"
    customer_id: str | None = None
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_payload(cls, body: dict[str, Any]) -> "EventParse":
        # Events without an id get a deterministic content hash; a literal "" would
        # collide on the (tenant_id, event_id) unique constraint and every id-less
        # event after the first would be silently dropped by the upsert.
        event_id = str(body.get("event_id") or body.get("id") or "")
        if not event_id:
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, default=str).encode()
            ).hexdigest()
            event_id = f"sha256:{digest}"
        return cls(
            tenant_id=str(body["tenant_id"]),
            device_id=str(body["device_id"]),
            event_id=event_id,
            event_type=str(body.get("event_type", "telemetry")),
            customer_id=str(body["customer_id"]) if body.get("customer_id") else None,
            time=datetime.fromtimestamp(int(body["ts"]) / 1000, UTC)
            if body.get("ts")
            else datetime.now(UTC),
            payload=body,
        )
