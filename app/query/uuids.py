"""One UUID test, shared.

The orchestrator and the fleet handlers both need to know whether an extracted
device_id is a real ThingsBoard id or a word the keyword extractor scraped after
"device" ("device category" -> "category"). Two copies of that check is how one of
them ends up permissive.
"""

from uuid import UUID


def is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True
