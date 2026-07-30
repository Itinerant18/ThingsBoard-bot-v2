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


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # The openers an operator types instead of a well-formed query. Every one of
        # these used to fall through to the generic device count.
        ("Is everything working fine right now?", "fleet_health"),
        ("Are there any issues I should know about?", "fleet_health"),
        ("What needs my attention today?", "fleet_health"),
        ("Show me what is broken", "fleet_health"),
        ("Which devices need immediate attention?", "fleet_health"),
        ("What is the most critical issue right now?", "alarm_detail"),
        ("How long has the current issue been going on?", "alarm_detail"),
        ("What happened in the system today?", "audit_log"),
        # Hierarchy reverse lookups and listings.
        ("Which ZO does BALLYBAZAR branch belong to?", "hierarchy_info"),
        ("Which FGMO region does ZO NASIK belong to?", "hierarchy_info"),
        ("What are all the FGMO regions in the BOI system?", "hierarchy_info"),
        ("How many total branches are there across all FGMO regions?", "hierarchy_info"),
        # A category count, which global_overview answered with the whole fleet.
        ("How many FAS devices are there?", "fleet_health"),
    ],
)
async def test_conversational_openers_and_reverse_lookups_route(
    question: str, expected: str
) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("battery voltage of liluah", "battery_voltage"),
        ("Is the CCTV system healthy?", "fleet_health"),
        ("Which branches are currently under the ODISHA zone?", "hierarchy_info"),
        ("How many total users are registered?", "user_directory"),
    ],
)
async def test_the_new_openers_do_not_capture_existing_questions(
    question: str, expected: str
) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # Real questions that answered "Name a device to check." — the operator was
        # asked to supply the very identifier they asked the bot to find.
        ("Which specific device is currently faulty?", "fleet_health"),
        ("List all devices that are currently faulty", "fleet_health"),
        ("Which CCTV cameras are currently faulty?", "cctv_fleet"),
        ("Which CCTV channels are currently disconnected?", "cctv_fleet"),
        ("Are there any Gateway system errors right now?", "fleet_health"),
        # Telemetry the fleet does not publish — say so instead of demanding an id.
        # NOT firmware: rock.firmwareVersion is published and derived.py reads it.
        ("What is the current uptime of all Gateway devices?", "unavailable_telemetry"),
        ("What is the current uptime of all CCTV devices?", "unavailable_telemetry"),
        ("What is the current disk utilization across all S-Vault nodes?", "unavailable_telemetry"),
        # Not telemetry at all.
        ("What does IAS stand for?", "unavailable_telemetry"),
        ("What should I do if a CCTV camera is disconnected?", "unavailable_telemetry"),
        ("What is the faulty device count trend over 30 days?", "unavailable_telemetry"),
    ],
)
async def test_fleet_shaped_questions_no_longer_demand_a_device_id(
    question: str, expected: str
) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # The fallback sits between the fleet handlers and the metric branches, so it
        # must not swallow what already worked on either side of that seam.
        ("Which branches are currently under the ODISHA zone?", "hierarchy_info"),
        ("Which branch is visible on the map?", "device_inventory"),
        ("battery voltage of liluah", "battery_voltage"),
        ("what is the cctv status of liluah", "cctv_status"),
        ("Is the CCTV system healthy?", "fleet_health"),
        ("Are there any active unresolved alarms?", "alarm_detail"),
    ],
)
async def test_the_fleet_fallback_does_not_capture_working_questions(
    question: str, expected: str
) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == expected


@pytest.mark.parametrize(
    "question",
    [
        "What is the device firmware version?",
        "What is the device serial number?",
        "What is the device model number?",
    ],
)
async def test_data_the_fleet_publishes_is_not_declined(question: str) -> None:
    """The first honest-decline pass over-reached: firmware, serial and model were
    added to the not-held list without checking, but derived.py reads
    rock.firmwareVersion / rock.serialNumber / rock.model. Declining data we hold
    misleads an operator exactly as much as inventing data we do not."""
    got = await KeywordIntentExtractor().extract(question)
    assert got.name != "unavailable_telemetry"
    assert got.name == "device_hardware"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # Same question, four phrasings. Only "belongs to" matched, so the rest fell
        # through to the generic device count.
        ("Which region does the HOWRAH ZO fall under?", "hierarchy_info"),
        ("Which ZO does BALLYBAZAR branch belong to?", "hierarchy_info"),
        ("BALLYBAZAR is part of which zone?", "hierarchy_info"),
        # Branch master data is not in ThingsBoard at all — decline, do not deflect.
        ("Who should I contact for device repair at BALLYBAZAR?", "unavailable_telemetry"),
        ("What is the branch address for BALLYBAZAR?", "unavailable_telemetry"),
        ("What is the pincode for LILUAH?", "unavailable_telemetry"),
        ("What is the escalation matrix?", "unavailable_telemetry"),
    ],
)
async def test_reverse_lookup_phrasings_and_branch_master_declines(
    question: str, expected: str
) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == expected


async def test_via_llm_distinguishes_a_real_llm_call_from_a_silent_fallback() -> None:
    # The extractor swallows every exception, so a dead API key produces the same
    # shape of answer as a working one. via_llm is the only thing that tells them
    # apart, and it is what the API's `used_llm` field now reports.
    llm_routed = await _extractor(
        '{"intent": "fleet_health", "device_id": null, "subsystem": null}'
    ).extract("how many devices are offline")
    assert llm_routed.via_llm is True

    fell_back = await _extractor(exc=RuntimeError("401 invalid api key")).extract(
        "how many devices are offline"
    )
    assert fell_back.via_llm is False, "keyword fallback must not claim the LLM ran"

    # An unroutable intent name is also a fallback, not an LLM success.
    bad_intent = await _extractor('{"intent": "not_a_real_intent"}').extract("hello there")
    assert bad_intent.via_llm is False
