import json
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
    result = await run_chat(payload, tenant, db, redis, request)

    async def events() -> AsyncIterator[dict[str, str]]:
        yield {"event": "answer", "data": json.dumps(result)}

    return EventSourceResponse(events())
