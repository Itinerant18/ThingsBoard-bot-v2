import pytest

from app.llm.intent import LlmIntentExtractor
from app.query.extract import KeywordIntentExtractor


class StubLlm:
    def __init__(self, text: str = "", exc: Exception | None = None) -> None:
        self._text = text
        self._exc = exc
        self.called = False

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        self.called = True
        if self._exc is not None:
            raise self._exc
        return self._text


def _extractor(text: str = "", exc: Exception | None = None) -> LlmIntentExtractor:
    return LlmIntentExtractor(StubLlm(text, exc), KeywordIntentExtractor())


async def test_valid_llm_json() -> None:
    result = await _extractor(
        '{"intent": "device_inventory", "device_id": "abc-1", "subsystem": null}'
    ).extract("what devices exist")
    assert result.name == "device_inventory"
    assert result.device_id == "abc-1"
    assert result.subsystem is None


async def test_fleet_health_is_an_allowed_llm_intent() -> None:
    result = await _extractor(
        '{"intent": "fleet_health", "device_id": null, "subsystem": "timeLock"}'
    ).extract("How many TLS devices are offline?")
    assert result.name == "fleet_health"
    assert result.subsystem == "timeLock"


async def test_fenced_json_is_tolerated() -> None:
    result = await _extractor(
        '```json\n{"intent": "subsystem_status", "device_id": "d9", "subsystem": "cctv"}\n```'
    ).extract("cctv status of d9")
    assert result.name == "subsystem_status"
    assert result.device_id == "d9"
    assert result.subsystem == "cctv"


async def test_llm_wins_over_keyword_when_they_disagree() -> None:
    # "overview of everything" -> keyword default global_overview; LLM says alarm_detail.
    # If the LLM branch silently degraded to the fallback, this would be global_overview.
    question = "overview of everything"
    assert (await KeywordIntentExtractor().extract(question)).name == "global_overview"
    result = await _extractor('{"intent": "alarm_detail"}').extract(question)
    assert result.name == "alarm_detail"


async def test_garbage_output_falls_back_to_keyword() -> None:
    # keyword classifier maps "list device" -> device_inventory.
    result = await _extractor("this is not json").extract("list device inventory")
    assert result.name == "device_inventory"


async def test_unhandled_intent_falls_back() -> None:
    # network_status is a real §2 intent but has no handler -> must not leak through.
    result = await _extractor('{"intent": "network_status"}').extract("any alarm right now")
    assert result.name == "alarm_detail"  # from keyword fallback


async def test_llm_error_falls_back() -> None:
    result = await _extractor(exc=RuntimeError("boom")).extract("any alarm right now")
    assert result.name == "alarm_detail"


async def test_blank_question_skips_llm() -> None:
    stub = StubLlm('{"intent": "device_inventory"}')
    extractor = LlmIntentExtractor(stub, KeywordIntentExtractor())
    result = await extractor.extract("   ")
    assert stub.called is False
    assert result.name == "global_overview"  # keyword default


@pytest.mark.parametrize(
    "question",
    [
        "what is the system current of liluah",
        "show me the current draw",
        "system current status",
    ],
)
async def test_amperage_questions_reach_the_current_metric(question: str) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == "system_current"


@pytest.mark.parametrize(
    "question",
    [
        "what is the current overall system compliance score?",
        "which zone currently has the worst SLA compliance?",
        "show me all users currently under the MP zone",
        "what is the most frequent error type currently occurring?",
    ],
)
async def test_current_the_adjective_is_not_an_ammeter_reading(question: str) -> None:
    """Matching the bare word "current" sent a fifth of the operator FAQ into the
    amperage handler. Answering a compliance question with an ammeter reading is a
    confident wrong answer, which is worse than admitting the question is unmapped."""
    assert (await KeywordIntentExtractor().extract(question)).name != "system_current"


@pytest.mark.parametrize(
    ("question", "prior_intent"),
    [
        # "it" hides inside "audit" — this went to whatever the previous turn was.
        ("Show me the audit logs", "user_directory"),
        ("What critical issues are open", "user_directory"),
        # Four words, but it opens like a question, so it is not a fragment.
        ("Who logged in recently", "fleet_health"),
        ("List all branches", "alarm_detail"),
    ],
)
async def test_a_self_contained_question_does_not_inherit_the_previous_intent(
    question: str, prior_intent: str
) -> None:
    """Found in production: a follow-up marker matched as a bare substring, and any
    question of four words or fewer counted as a fragment."""
    from app.query.memory import ChatContext

    got = await KeywordIntentExtractor().extract(question, ChatContext(intent=prior_intent))
    assert got.name != prior_intent


@pytest.mark.parametrize(
    "question",
    ["and howrah?", "why?", "what about liluah", "and yesterday?"],
)
async def test_real_fragments_still_inherit(question: str) -> None:
    from app.query.memory import ChatContext

    got = await KeywordIntentExtractor().extract(question, ChatContext(intent="battery_voltage"))
    assert got.name == "battery_voltage"


async def test_a_word_scraped_after_device_is_not_treated_as_a_device_id() -> None:
    """"Which device category ..." yielded device_id="category", and the fleet
    handlers then refused the question as an unauthorized device."""
    from app.query.handlers import _requested_device

    got = await KeywordIntentExtractor().extract(
        "Which device category has the most offline devices?"
    )
    assert got.device_id == "category"  # the extractor still scrapes it
    requested, refuse = _requested_device(got, ["0d1f8a10-2833-11f1-afd7-eb430bfb427f"])
    assert (requested, refuse) == (None, False)  # ...but no handler acts on it


async def test_a_real_uuid_outside_scope_is_still_refused() -> None:
    from app.query.contracts import ExtractedIntent
    from app.query.handlers import _requested_device

    intent = ExtractedIntent(
        name="fleet_health",
        device_id="ffffffff-2833-11f1-afd7-eb430bfb427f",
        raw_question="health of that device",
    )
    assert _requested_device(intent, ["0d1f8a10-2833-11f1-afd7-eb430bfb427f"]) == (None, True)
