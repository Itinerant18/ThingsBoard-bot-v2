import uuid
from types import SimpleNamespace

from app.auth.jwt import TenantContext
from app.hierarchy.scope import ScopedBranches
from app.query.branch_names import (
    BranchDirectory,
    BranchEntry,
    BranchGateResult,
    alias_variants,
    gate_and_resolve,
    normalize_key,
)
from app.query.contracts import Answer, ExtractedIntent, RequestContext
from app.query.orchestrate import QueryOrchestrator

D1 = str(uuid.uuid4())  # LILUAH (in scope)
D2 = str(uuid.uuid4())  # MALDA TOWN (in scope)
D3 = str(uuid.uuid4())  # CONNAUGHT (out of scope)
D4 = str(uuid.uuid4())  # MALDA TOWN MAIN (out of scope)


def directory() -> BranchDirectory:
    return BranchDirectory(
        prefix="BOI",
        leaves=(
            BranchEntry("BOI-LILUAH", "BOI-LILUAH", D1),
            BranchEntry("BOI-MALDATOWN", "Branch MALDA TOWN", D2),
            BranchEntry("BOI-CONNAUGHT", "BOI-CONNAUGHT", D3),
            BranchEntry("BOI-MALDATOWNMAIN", "MALDA TOWN MAIN", D4),
            BranchEntry("BOI-HOWRAH", "BOI-HOWRAH", str(uuid.uuid4())),  # out of scope
        ),
        zones=(
            BranchEntry("ZO-KOLKATA", "ZO Kolkata", None),
            BranchEntry("ZO-DELHI", "ZO Delhi", None),
            BranchEntry("ZO-HOWRAH", "ZO Howrah", None),
        ),
    )


def scoped() -> ScopedBranches:
    return ScopedBranches(
        branch_node_ids=["BOI-LILUAH", "BOI-MALDATOWN"], tb_device_ids=[D1, D2]
    )


def test_normalize_key_generic_prefix() -> None:
    assert normalize_key("BOI-MALDA_TOWN", "BOI") == "MALDA TOWN"
    assert normalize_key("Branch  Liluah", "BOI") == "LILUAH"
    assert normalize_key("PNB-KANPUR", "PNB") == "KANPUR"
    assert normalize_key(None, "BOI") == ""


def test_alias_variants_device_suffix() -> None:
    variants = alias_variants("BOI-LILUAH TESTING DEVICE", "BOI")
    assert "LILUAH" in variants
    assert "LILUAH TESTING DEVICE" in variants


def test_gate_flags_out_of_scope_branch() -> None:
    result = gate_and_resolve("battery voltage of Connaught", directory(), scoped())
    assert result.unauthorized_branch == "BOI-CONNAUGHT"
    assert result.device_id is None


def test_gate_beats_resolution_longest_name_first() -> None:
    # "malda town main" contains in-scope "MALDA TOWN" too; the longer out-of-scope
    # name must win, refusing instead of silently answering for the wrong branch.
    result = gate_and_resolve("gateway status of malda town main", directory(), scoped())
    assert result.unauthorized_branch == "MALDA TOWN MAIN"


def test_zone_phrase_not_flagged() -> None:
    # HOWRAH the branch is out of scope, but "ZO HOWRAH" names the zone container.
    result = gate_and_resolve("gateway status for ZO Howrah", directory(), scoped())
    assert result.unauthorized_branch is None


def test_zone_name_is_always_safe_to_mention() -> None:
    result = gate_and_resolve("list branches under ZO Delhi", directory(), scoped())
    assert result.unauthorized_branch is None


def test_resolves_in_scope_branch_to_device() -> None:
    result = gate_and_resolve("battery voltage of Liluah", directory(), scoped())
    assert result.device_id == D1
    assert result.branch_name == "BOI-LILUAH"


def test_resolves_compact_spelling() -> None:
    # Display name "Branch MALDA TOWN"; user types it without the space.
    result = gate_and_resolve("cctv status of maldatown", directory(), scoped())
    assert result.device_id == D2


def test_no_branch_mentioned() -> None:
    result = gate_and_resolve("how many devices are online", directory(), scoped())
    assert result == BranchGateResult()


# --- orchestrator wiring ------------------------------------------------------


class _CaptureHandler:
    def __init__(self) -> None:
        self.seen: ExtractedIntent | None = None

    async def can_handle(self, intent: ExtractedIntent) -> bool:
        return True

    async def handle(self, intent: ExtractedIntent, ctx: RequestContext) -> Answer:
        self.seen = intent
        return Answer("ok")


def make_ctx() -> RequestContext:
    tenant = TenantContext(
        tenant_id="tt", customer_id="c", subject="s", claims={}, scopes=(),
        region=None, prefix="BOI", user_token="tok",
    )
    return RequestContext(tenant=tenant, db=SimpleNamespace(), redis=SimpleNamespace(), tb=SimpleNamespace())  # type: ignore[arg-type]


async def test_orchestrator_refuses_unauthorized_before_dispatch() -> None:
    async def gate(question: str, ctx: RequestContext) -> BranchGateResult:
        return BranchGateResult(unauthorized_branch="BOI-CONNAUGHT")

    capture = _CaptureHandler()
    orch = QueryOrchestrator(gate=gate)
    orch.handlers = [capture]
    answer = await orch.ask("battery voltage of Connaught", make_ctx())
    assert "not authorized" in answer.text
    assert capture.seen is None  # never reached a handler


async def test_orchestrator_injects_resolved_device() -> None:
    async def gate(question: str, ctx: RequestContext) -> BranchGateResult:
        return BranchGateResult(device_id=D1, branch_name="BOI-LILUAH")

    capture = _CaptureHandler()
    orch = QueryOrchestrator(gate=gate)
    orch.handlers = [capture]
    answer = await orch.ask("battery voltage of Liluah", make_ctx())
    assert answer.text == "ok"
    assert capture.seen is not None
    assert capture.seen.device_id == D1
    assert capture.seen.node_name == "BOI-LILUAH"


def test_gate_refuses_even_when_in_scope_branch_also_named() -> None:
    # Mixing an authorized and an unauthorized branch in one question must refuse.
    result = gate_and_resolve(
        "compare battery of Liluah and Connaught", directory(), scoped()
    )
    assert result.unauthorized_branch == "BOI-CONNAUGHT"


async def test_orchestrator_gate_overrides_non_uuid_extractor_device() -> None:
    # An LLM extractor may echo the branch NAME into device_id; the gate's scoped
    # UUID must win over that free text.
    async def gate(question: str, ctx: RequestContext) -> BranchGateResult:
        return BranchGateResult(device_id=D1, branch_name="BOI-LILUAH")

    class _NameEchoExtractor:
        async def extract(self, question: str, context: object = None) -> ExtractedIntent:
            return ExtractedIntent(name="battery_voltage", device_id="Liluah")

    capture = _CaptureHandler()
    orch = QueryOrchestrator(extractor=_NameEchoExtractor(), gate=gate)
    orch.handlers = [capture]
    await orch.ask("battery voltage of Liluah", make_ctx())
    assert capture.seen is not None
    assert capture.seen.device_id == D1


async def test_orchestrator_keeps_explicit_device_id() -> None:
    explicit = str(uuid.uuid4())

    async def gate(question: str, ctx: RequestContext) -> BranchGateResult:
        return BranchGateResult(device_id=D1, branch_name="BOI-LILUAH")

    capture = _CaptureHandler()
    orch = QueryOrchestrator(gate=gate)
    orch.handlers = [capture]
    await orch.ask(f"battery voltage of device {explicit}", make_ctx())
    assert capture.seen is not None
    assert capture.seen.device_id == explicit  # user's explicit id wins
