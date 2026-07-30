from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TenantContext

if TYPE_CHECKING:
    from app.query.timeframe import TimeWindow
from app.clients.thingsboard import ThingsBoardClient


@dataclass(frozen=True)
class ExtractedIntent:
    name: str
    device_id: str | None = None
    node_name: str | None = None
    subsystem: str | None = None
    raw_question: str = ""
    # Set when the question asks about a PERIOD rather than the current value; the
    # metric handler then answers from device_telemetry instead of a live TB fetch.
    window: "TimeWindow | None" = None
    # True only when the LLM actually classified this question. LlmIntentExtractor
    # swallows every exception and falls back to the keyword classifier, so a broken
    # API key looks exactly like a working one from the outside — this is the field
    # that tells them apart.
    via_llm: bool = False


@dataclass
class Answer:
    text: str
    structured: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, str]] = field(default_factory=list)
    used_llm: bool = False


@dataclass
class RequestContext:
    tenant: TenantContext
    db: AsyncSession
    redis: Redis
    tb: ThingsBoardClient


class Handler(Protocol):
    # Dispatch is by can_handle(); a handler may serve one intent (with a class-level
    # `intent` attr) or many (MetricHandler), so the protocol requires only the methods.
    async def can_handle(self, intent: ExtractedIntent) -> bool: ...
    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer: ...
