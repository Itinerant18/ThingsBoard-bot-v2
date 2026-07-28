"""Follow-up questions must inherit the previous turn's subject.

Phase 1 of conversational memory. The history was already written to Redis on every
turn by record_turn() and then read by NOBODY: load_context() was called once, purely
for the active device id, and the extractor's signature was extract(question) — so
every question was classified in isolation.

The visible symptom: "and last week?" carries no intent words, falls through the
keyword chain to its global_overview default, and answers a question nobody asked.

Production runs the KEYWORD extractor (OPENAI_API_KEY is unset), so these cover that
path first; the LLM path gets the same context as chat messages.
"""

import pytest

from app.query.extract import KeywordIntentExtractor, _is_fragment
from app.query.memory import ChatContext


async def intent_for(question: str, context: ChatContext | None = None) -> str:
    return (await KeywordIntentExtractor().extract(question, context)).name


class TestFragmentDetection:
    @pytest.mark.parametrize(
        "text",
        ["and last week?", "why?", "what about howrah", "same for that one", "compare it"],
    )
    def test_fragments_are_detected(self, text: str) -> None:
        assert _is_fragment(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "battery voltage of liluah",
            "cctv status",
            "how many devices do i have",
            "network operator",
        ],
    )
    def test_self_contained_questions_are_not_fragments(self, text: str) -> None:
        """A false positive answers the WRONG question, so anything naming its own
        subject must classify normally even when it also reads like a follow-up."""
        assert _is_fragment(text) is False

    def test_and_prefixed_but_self_contained_is_not_a_fragment(self) -> None:
        assert _is_fragment("and the cctv status of liluah") is False

    def test_empty_is_not_a_fragment(self) -> None:
        assert _is_fragment("") is False


class TestInheritance:
    @pytest.mark.asyncio
    async def test_fragment_inherits_the_previous_intent(self) -> None:
        context = ChatContext(intent="battery_voltage")
        assert await intent_for("and last week?", context) == "battery_voltage"
        assert await intent_for("why?", context) == "battery_voltage"

    @pytest.mark.asyncio
    async def test_without_context_a_fragment_falls_back_to_the_default(self) -> None:
        """The pre-Phase-1 behaviour, kept explicit: with nothing remembered there is
        nothing to inherit."""
        assert await intent_for("and last week?") == "global_overview"

    @pytest.mark.asyncio
    async def test_a_self_contained_question_overrides_the_remembered_intent(self) -> None:
        """Memory must not hijack a question that states its own subject."""
        context = ChatContext(intent="battery_voltage")
        assert await intent_for("cctv status", context) == "cctv_status"
        assert await intent_for("how many devices do i have", context) == "global_overview"

    @pytest.mark.asyncio
    async def test_subsystem_in_a_fragment_is_still_extracted(self) -> None:
        context = ChatContext(intent="subsystem_status")
        got = await KeywordIntentExtractor().extract("and the bas?", context)
        assert got.name == "subsystem_status"
        assert got.subsystem == "bas"


class TestLlmHistory:
    def test_history_becomes_ordered_chat_messages(self) -> None:
        """The window is passed in ORDER and in full — which is exactly why a sliding
        window beats a vector search for short-term context."""
        from app.llm.intent import _history_messages

        context = ChatContext(
            history=(
                ("user", "battery voltage of liluah"),
                ("assistant", "Battery voltage is 14.0."),
            )
        )
        assert _history_messages(context) == [
            {"role": "user", "content": "battery voltage of liluah"},
            {"role": "assistant", "content": "Battery voltage is 14.0."},
        ]

    def test_no_context_sends_no_history(self) -> None:
        from app.llm.intent import _history_messages

        assert _history_messages(None) == []
        assert _history_messages(ChatContext()) == []
