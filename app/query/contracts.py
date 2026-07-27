from dataclasses import dataclass, field
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TenantContext
from app.clients.thingsboard import ThingsBoardClient


@dataclass(frozen=True)
class ExtractedIntent:
    name: str
    device_id: str | None = None
    node_name: str | None = None
    subsystem: str | None = None
    raw_question: str = ""


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
