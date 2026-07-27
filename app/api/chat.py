import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.jwt import TenantContext
from app.deps import current_tenant, get_db, get_redis
from app.query.contracts import RequestContext
from app.query.memory import session_key

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str = "default"


async def run_chat(
    payload: ChatRequest, tenant: TenantContext, db: AsyncSession, redis: Redis, request: Request
) -> dict[str, object]:
    ctx = RequestContext(tenant=tenant, db=db, redis=redis, tb=request.app.state.tb)
    # Session key is bound to the VERIFIED tenant + subject; the client only picks the
    # conversation_id suffix, so it can never address another user's memory. No subject
    # means no per-user identity — then memory is disabled rather than shared.
    session = (
        session_key(tenant.tenant_id, tenant.subject, payload.conversation_id)
        if tenant.subject
        else None
    )
    answer = await request.app.state.orchestrator.ask(payload.message, ctx, session_id=session)
    return {
        "answer": answer.text,
        "structured": answer.structured,
        "sources": answer.sources,
        "used_llm": answer.used_llm,
    }


@router.post("/api/v1/chat")
async def chat(
    request: Request,
    payload: ChatRequest,
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict[str, object]:
    return await run_chat(payload, tenant, db, redis, request)


@router.post("/ask")
async def ask(
    request: Request,
    payload: ChatRequest,
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict[str, object]:
    return await run_chat(payload, tenant, db, redis, request)


@router.post("/ask/stream")
async def ask_stream(
    request: Request,
    payload: ChatRequest,
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> EventSourceResponse:
    """SSE endpoint consumed by the chat widget in frontend/.

    The widget's frame parser only acts on `token`, `done` and `error`; any other
    event name is dropped silently, so it would render an empty bubble rather than
    an error. The event names below are therefore part of the contract — see
    frontend/src/context/ChatContext.tsx.

    There are no `token` frames: orchestrator.ask() computes a complete answer
    before returning, so there is nothing to stream incrementally. A `done`-only
    stream renders correctly (the widget finalizes on `done` whether or not tokens
    preceded it); emitting fake token chunks would only simulate streaming.

    NOTE: the widget also sends X-TB-Host. It is deliberately NOT read here —
    ThingsBoard's base URL comes from settings and is validated by
    assert_allowed_tb_url, and honouring a client-supplied host would be an SSRF
    hole.
    """

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            result = await run_chat(payload, tenant, db, redis, request)
        except Exception:
            logger.exception("chat stream failed")
            # The stream has already begun (200 + headers sent), so an exception
            # cannot become an HTTP error status — it must be reported in-band or
            # the widget hangs on a typing indicator until the socket closes.
            yield {
                "event": "error",
                "data": json.dumps({"errorMessage": "Something went wrong answering that."}),
            }
            return
        yield {
            "event": "done",
            "data": json.dumps(
                {
                    **result,
                    # The widget reads these two explicitly; leaving them undefined
                    # makes it render the error branch on a perfectly good answer.
                    "error": False,
                    "timestamp": int(time.time() * 1000),
                }
            ),
        }

    return EventSourceResponse(events())
