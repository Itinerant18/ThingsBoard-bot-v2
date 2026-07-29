import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Protocol

from app.auth.scope_resolver import PermissionCheckUnavailable, resolved_scope
from app.auth.tb_acl import SessionExpired
from app.query import memory
from app.query.branch_names import BranchGateResult, gate_and_resolve, load_directory
from app.query.contracts import Answer, ExtractedIntent, Handler, RequestContext
from app.query.extract import KeywordIntentExtractor
from app.query.handlers import (
    AlarmDetail,
    AuditLog,
    CctvFleet,
    DeviceInventory,
    FleetHealth,
    GlobalOverview,
    HierarchyInfo,
    MetricHandler,
    UserDirectory,
)
from app.query.uuids import is_uuid as _is_uuid

logger = logging.getLogger(__name__)


class _Extractor(Protocol):
    # `context` is optional so a test double can still be a bare one-arg callable.
    async def extract(
        self, question: str, context: memory.ChatContext | None = None
    ) -> ExtractedIntent: ...


GateFn = Callable[[str, RequestContext], Awaitable[BranchGateResult]]





async def _default_gate(question: str, ctx: RequestContext) -> BranchGateResult:
    """Scan the question against the customer's full branch directory. No prefix means
    no hierarchy to check against — the per-device scope gate in MetricHandler still
    denies any actual data access."""
    if not ctx.tenant.prefix:
        return BranchGateResult()
    directory = await load_directory(ctx.db, ctx.tenant.prefix)
    if not directory.leaves:
        return BranchGateResult()
    # Same resolver as chat handlers and the HTTP endpoints. Using raw branch_scope
    # here would let the gate resolve a branch name to a device ThingsBoard does not
    # authorize, so the name gate and the data gate would disagree about scope.
    scoped = await resolved_scope(ctx.db, ctx.redis, ctx.tenant, ctx.tb.settings)
    return gate_and_resolve(question, directory, scoped)


class QueryOrchestrator:
    def __init__(self, extractor: "_Extractor | None" = None, gate: GateFn | None = None) -> None:
        self.extractor: _Extractor = extractor or KeywordIntentExtractor()
        self.gate: GateFn = gate or _default_gate
        self.handlers: list[Handler] = [
            GlobalOverview(),
            DeviceInventory(),
            HierarchyInfo(),
            FleetHealth(),
            CctvFleet(),
            UserDirectory(),
            AuditLog(),
            AlarmDetail(),
            MetricHandler(),
        ]

    async def ask(self, question: str, ctx: RequestContext, session_id: str | None = None) -> Answer:
        # SECURITY: the unauthorized-branch gate runs BEFORE intent dispatch, so naming
        # a branch outside the caller's scope is refused for every intent (Java parity:
        # UserDataService.detectUnauthorizedBranchName ran ahead of answering).
        try:
            return await self._ask(question, ctx, session_id)
        except SessionExpired:
            # Distinct from the branch below on purpose: retrying a dead token can
            # never succeed, so telling the user to "retry in a moment" wastes their
            # time on the one failure that always needs a human action.
            logger.info("[TB-ACL] caller token rejected by ThingsBoard")
            return Answer(
                "Your ThingsBoard session has expired or was signed out. "
                "Please sign in to ThingsBoard again and reload this page — "
                "retrying will not help until you do.",
                {"error": "session_expired"},
            )
        except PermissionCheckUnavailable:
            # Fail CLOSED. The local hierarchy over-grants (a customer prefix spans
            # several ThingsBoard customers), so answering from it when TB cannot
            # confirm permissions is exactly the leak the ACL check prevents.
            logger.warning("[TB-ACL] refusing to answer: permissions unconfirmed", exc_info=True)
            return Answer(
                "I could not confirm your permissions with ThingsBoard just now, so I "
                "will not answer rather than risk showing you something outside your "
                "access. Please retry in a moment.",
                {"error": "permissions_unavailable"},
            )

    async def _ask(
        self, question: str, ctx: RequestContext, session_id: str | None = None
    ) -> Answer:
        gate = await self.gate(question, ctx)
        if gate.unauthorized_branch is not None:
            return Answer(
                f"You are not authorized to access branch '{gate.unauthorized_branch}'.",
                {"unauthorized_branch": gate.unauthorized_branch},
            )
        # Load the session context ONCE and pass it to the extractor. The history was
        # already being written on every turn and then never read: the extractor saw
        # each question in isolation, so anything that leaned on the previous turn
        # ("and last week?", "why?") resolved to the default intent.
        remembered = (
            await memory.load_context(ctx.redis, session_id) if session_id else memory.ChatContext()
        )

        intent = await self.extractor.extract(question, remembered)
        if gate.device_id is not None and not _is_uuid(intent.device_id):
            # The extractor's device_id is free text (an LLM may echo a branch NAME
            # there); only a real UUID may override the gate's scoped resolution.
            intent = replace(intent, device_id=gate.device_id, node_name=gate.branch_name)
        elif intent.device_id is None and remembered.device_id is not None:
            # Follow-up ("and its battery?"): no branch in this question — fall back to
            # the session's active branch. It was resolved within THIS user's scope, and
            # MetricHandler re-verifies scope on every call, so a stale scope cannot leak.
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
            # Remember the resolved intent so the NEXT fragment can inherit it. Only
            # intents that actually answered something — a failed lookup is a poor
            # subject for "and last week?" to attach to.
            if answer.structured.get("error") is None:
                await memory.set_active_intent(ctx.redis, session_id, intent.name)
                if intent.window is not None:
                    await memory.set_active_window(
                        ctx.redis, session_id, intent.window.hours, intent.window.label
                    )
            if gate.device_id is not None:
                await memory.set_active_branch(
                    ctx.redis, session_id, gate.device_id, gate.branch_name
                )
            elif intent.device_id is not None and _is_uuid(intent.device_id):
                await memory.set_active_branch(
                    ctx.redis, session_id, intent.device_id, intent.node_name
                )
        return answer
