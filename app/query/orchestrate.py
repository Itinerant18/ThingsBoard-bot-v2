from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Protocol
from uuid import UUID

from app.hierarchy.scope import branch_scope, extract_region
from app.query import memory
from app.query.branch_names import BranchGateResult, gate_and_resolve, load_directory
from app.query.contracts import Answer, ExtractedIntent, Handler, RequestContext
from app.query.extract import KeywordIntentExtractor
from app.query.handlers import AlarmDetail, DeviceInventory, GlobalOverview, MetricHandler


class _Extractor(Protocol):
    async def extract(self, question: str) -> ExtractedIntent: ...


GateFn = Callable[[str, RequestContext], Awaitable[BranchGateResult]]


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


async def _default_gate(question: str, ctx: RequestContext) -> BranchGateResult:
    """Scan the question against the customer's full branch directory. No prefix means
    no hierarchy to check against — the per-device scope gate in MetricHandler still
    denies any actual data access."""
    if not ctx.tenant.prefix:
        return BranchGateResult()
    directory = await load_directory(ctx.db, ctx.tenant.prefix)
    if not directory.leaves:
        return BranchGateResult()
    scoped = await branch_scope(
        ctx.db, ctx.tenant.prefix, extract_region(ctx.tenant.claims), ctx.redis
    )
    return gate_and_resolve(question, directory, scoped)


class QueryOrchestrator:
    def __init__(self, extractor: "_Extractor | None" = None, gate: GateFn | None = None) -> None:
        self.extractor: _Extractor = extractor or KeywordIntentExtractor()
        self.gate: GateFn = gate or _default_gate
        self.handlers: list[Handler] = [
            GlobalOverview(),
            DeviceInventory(),
            AlarmDetail(),
            MetricHandler(),
        ]

    async def ask(self, question: str, ctx: RequestContext, session_id: str | None = None) -> Answer:
        # SECURITY: the unauthorized-branch gate runs BEFORE intent dispatch, so naming
        # a branch outside the caller's scope is refused for every intent (Java parity:
        # UserDataService.detectUnauthorizedBranchName ran ahead of answering).
        gate = await self.gate(question, ctx)
        if gate.unauthorized_branch is not None:
            return Answer(
                f"You are not authorized to access branch '{gate.unauthorized_branch}'.",
                {"unauthorized_branch": gate.unauthorized_branch},
            )
        intent = await self.extractor.extract(question)
        if gate.device_id is not None and not _is_uuid(intent.device_id):
            # The extractor's device_id is free text (an LLM may echo a branch NAME
            # there); only a real UUID may override the gate's scoped resolution.
            intent = replace(intent, device_id=gate.device_id, node_name=gate.branch_name)
        elif session_id and intent.device_id is None:
            # Follow-up ("and its battery?"): no branch in this question — fall back to
            # the session's active branch. It was resolved within THIS user's scope, and
            # MetricHandler re-verifies scope on every call, so a stale scope cannot leak.
            remembered = await memory.load_context(ctx.redis, session_id)
            if remembered.device_id is not None:
                intent = replace(
                    intent, device_id=remembered.device_id, node_name=remembered.branch_name
                )

        answer: Answer | None = None
        for handler in self.handlers:
            if await handler.can_handle(intent):
                answer = await handler.handle(intent, ctx)
                break
        if answer is None:
            answer = Answer("I could not map that question to a supported fleet query.")

        if session_id:
            await memory.record_turn(ctx.redis, session_id, question, answer.text)
            if gate.device_id is not None:
                await memory.set_active_branch(
                    ctx.redis, session_id, gate.device_id, gate.branch_name
                )
            elif intent.device_id is not None and _is_uuid(intent.device_id):
                await memory.set_active_branch(
                    ctx.redis, session_id, intent.device_id, intent.node_name
                )
        return answer
