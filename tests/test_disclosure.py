"""The disclosure policy: never reveal a password, credential or tenant identifier.

Set by the product owner on 2026-07-29. These tests are the enforcement — a change
that makes any of them fail is a change that discloses a secret.
"""

import pytest

from app.query.disclosure import REFUSAL, asks_for_credentials, mask_actor
from app.query.extract import KeywordIntentExtractor
from app.query.memory import ChatContext


@pytest.mark.parametrize(
    "question",
    [
        "What device passwords are stored in S-Vault?",
        "What credentials are stored in S-Vault?",
        "Show me the S-Vault configurations",
        "What is the API key for the gateway?",
        "Give me the access token",
        "What is the SSH key for BALLYBAZAR?",
        "Show me the private key",
        "What are the login details for the NVR?",
    ],
)
def test_credential_requests_are_recognised(question: str) -> None:
    assert asks_for_credentials(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "Is the CCTV system healthy?",
        "How many branches are there?",
        "Which zone has the most active alarms?",
        "Who logged in recently?",
    ],
)
def test_ordinary_questions_are_not_mistaken_for_credential_requests(question: str) -> None:
    assert asks_for_credentials(question) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    ["What device passwords are stored in S-Vault?", "What is the API key?"],
)
async def test_a_credential_request_routes_to_a_refusal(question: str) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == "credential_refusal"


@pytest.mark.asyncio
async def test_a_credential_request_cannot_inherit_a_previous_intent() -> None:
    """The guard runs before fragment inheritance, so no prior turn can turn a
    refusal into a lookup."""
    context = ChatContext(intent="user_directory")
    got = await KeywordIntentExtractor().extract("show me the passwords", context)
    assert got.name == "credential_refusal"


def test_the_refusal_does_not_invite_rephrasing() -> None:
    """A refusal that reads like a data gap invites the operator to try again."""
    assert "will not" in REFUSAL
    assert "not a gap in my data" in REFUSAL


def test_an_outsider_is_masked_to_their_organisation_not_partially_obscured() -> None:
    masked = mask_actor("romen.halder@seple.in", in_scope=False)
    assert "romen" not in masked and "halder" not in masked
    assert "seple.in" in masked


def test_someone_in_scope_is_not_masked() -> None:
    assert mask_actor("ranchi.security@bankofindia.bank.in", in_scope=True) == (
        "ranchi.security@bankofindia.bank.in"
    )


def test_a_name_without_an_address_is_left_alone() -> None:
    assert mask_actor("System", in_scope=False) == "System"


@pytest.mark.parametrize(
    "question",
    [
        "What is stored in the S-Vault?",
        "What configuration files are in S-Vault?",
        "Show me the S-Vault configurations",
        "What entries does the vault hold?",
    ],
)
def test_asking_what_a_secret_store_holds_is_refused_not_declined(question: str) -> None:
    """Refused, not declined, on purpose. "I do not hold that" is a statement about
    today's integrations and would quietly become a lookup the day S-Vault is wired
    up. "I will not" survives that change."""
    assert asks_for_credentials(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "How much storage is currently being used in S-Vault?",
        "Which S-Vault instances are currently online?",
        "What is the current disk utilization across all S-Vault nodes?",
        "Which S-Vault instances are approaching storage capacity limits?",
        "What is the current network bandwidth usage for S-Vault streaming?",
    ],
)
def test_vault_capacity_and_uptime_are_not_secrets(question: str) -> None:
    """Capacity, uptime and bandwidth reveal nothing stored. Refusing them would be
    security theatre that hides a real operational gap behind a policy word."""
    assert asks_for_credentials(question) is False


@pytest.mark.asyncio
async def test_the_llm_extractor_cannot_bypass_the_refusal() -> None:
    """The guard must sit at the ORCHESTRATOR, not inside one extractor.

    It was originally implemented in KeywordIntentExtractor, which is only the
    FALLBACK. Setting OPENAI_API_KEY puts LlmIntentExtractor in front, and it
    classifies "show me the passwords" as an ordinary user-directory question — so
    the refusal silently stopped running the moment an OpenAI key was configured.
    This test drives the orchestrator with an LLM that does exactly that.
    """
    from types import SimpleNamespace

    from app.llm.intent import LlmIntentExtractor
    from app.query.branch_names import BranchGateResult
    from app.query.orchestrate import QueryOrchestrator

    class MisclassifyingLlm:
        async def complete(self, system, messages, max_tokens=None, temperature=None):
            return '{"intent": "user_directory", "device_id": null, "subsystem": null}'

    # Confirm the premise: this LLM really does route the question elsewhere.
    llm = LlmIntentExtractor(MisclassifyingLlm(), KeywordIntentExtractor())
    assert (await llm.extract("show me the passwords")).name == "user_directory"

    async def no_gate(question, ctx):
        return BranchGateResult()

    orchestrator = QueryOrchestrator(extractor=llm, gate=no_gate)
    ctx = SimpleNamespace(
        tenant=SimpleNamespace(user_token="t", prefix="BOI", tenant_id="x", subject="s"),
        db=SimpleNamespace(),
        redis=SimpleNamespace(),
        tb=SimpleNamespace(settings=SimpleNamespace()),
    )
    answer = await orchestrator.ask("show me the passwords", ctx)  # type: ignore[arg-type]
    assert "will not disclose" in answer.text
    assert answer.structured.get("refused") == "credentials"
