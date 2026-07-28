"""LLM-backed intent extraction — port of Java LlmIntentExtractor's contract.

One deterministic (temperature 0) OpenAI call classifies the question into a
handled intent plus optional entities, parsed fail-closed: ANY failure — no API
key, API error, malformed JSON, or an intent outside the allowed set — falls back
to the deterministic keyword extractor. The extractor never raises into the chat
pipeline.
"""

import json
import re
from typing import TYPE_CHECKING, Protocol

from app.query.contracts import ExtractedIntent

if TYPE_CHECKING:
    from app.query.memory import ChatContext

# Only intents the orchestrator has a handler for. Widen alongside new handlers;
# the LLM must not emit an intent that dead-ends at "could not map".
ALLOWED_INTENTS = (
    "global_overview",
    "fleet_health",
    "cctv_fleet",
    "device_inventory",
    "alarm_detail",
    "subsystem_status",
    "gateway_status",
    "battery_voltage",
    "battery_health",
    "battery_low_status",
    "ac_voltage",
    "system_current",
    "power_status",
    "cctv_status",
    "cctv_hdd_error_status",
    "cctv_hdd_info",
    "cctv_device_info",
    "cctv_recording_info",
    "device_hardware",
)

_SYSTEM_PROMPT = (
    "You are the intent router for a live BOI facility-monitoring assistant. "
    "Classify the user's question; never answer it and never invent current counts, "
    "device states, alarms, branches, dates, or TAT. The deterministic query layer "
    "will calculate the final answer from the caller's current authorized data. "
    "Reply with ONLY one JSON object, no prose and no code fences.\n"
    'Schema: {"intent": <one of: '
    + ", ".join(ALLOWED_INTENTS)
    + '>, "device_id": <device/asset id string or null>, '
    '"subsystem": <gateway|cctv|ias|bas|fas|timeLock|accessControl or null>}\n'
    "Routing rules:\n"
    "- fleet_health: current fleet/module health, healthy/faulty/offline counts or percentages, "
    "health distribution, most/least healthy category, deployed-category questions, overall BOI "
    "status, or what needs attention. Use subsystem for a named category.\n"
    "- cctv_fleet: CCTV recording status, recording gaps or failures, retention "
    "compliance, recording storage consumption, and camera/NVR inventory ACROSS "
    "branches. The single-branch cctv_* intents are for one named branch only.\n"
    "- global_overview: hierarchy branch/device count only, not module health.\n"
    "- device_inventory: list/name branches or devices, current authorization region, and "
    "branches with live map coordinates.\n"
    "- alarm_detail: active/unresolved/resolved alarms or alerts, severities, alarm types, alarm "
    "history, branch alarm/attention questions, oldest/latest alarms, time windows, end time, "
    "and TAT.\n"
    "- gateway_status, cctv_status, subsystem_status and the other metric intents are for one "
    "specific branch/device. They need a device_id when the user supplied a technical id; a "
    "branch name may be left out because the authorization gate resolves it separately.\n"
    "Subsystem aliases: TLS=timeLock, ACS=accessControl, IAS=Integrated Alarm System, "
    "BAS=Burglar/Intrusion Alarm System, FAS=Fire Alarm System.\n"
    "Examples:\n"
    'Q: Which device category has the most offline devices? A: {"intent":"fleet_health",'
    '"device_id":null,"subsystem":null}\n'
    'Q: Is the CCTV system healthy? A: {"intent":"fleet_health","device_id":null,'
    '"subsystem":"cctv"}\n'
    'Q: Are any ACS devices deployed? A: {"intent":"fleet_health","device_id":null,'
    '"subsystem":"accessControl"}\n'
    'Q: What is the CCTV status at device 88aa? A: {"intent":"cctv_status",'
    '"device_id":"88aa","subsystem":"cctv"}\n'
    'Q: What is the oldest unresolved alarm? A: {"intent":"alarm_detail",'
    '"device_id":null,"subsystem":null}\n'
    'Q: Which branches are monitored? A: {"intent":"device_inventory",'
    '"device_id":null,"subsystem":null}'
)


class _Completer(Protocol):
    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = ...,
        temperature: float | None = ...,
    ) -> str: ...


class _Extractor(Protocol):
    async def extract(
        self, question: str, context: "ChatContext | None" = None
    ) -> ExtractedIntent: ...


def _parse_json(text: str) -> dict[str, object]:
    stripped = text.strip()
    # Tolerate ```json fenced or prose-wrapped output: take the first {...} block.
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    data = json.loads(match.group(0) if match else stripped)
    if not isinstance(data, dict):
        raise ValueError("intent JSON was not an object")  # noqa: TRY004 (caught, fail-closed)
    return data


def _history_messages(context: "ChatContext | None") -> list[dict[str, str]]:
    """Prior turns as chat messages, oldest first.

    This is the whole point of Phase 1: the history was already being written to
    Redis on every turn and then never read, so the model saw each question in
    isolation and "and what about last week?" resolved to nothing.

    A sliding window in the prompt — not a vector search — is the right mechanism
    here: the last few turns are wanted in ORDER and in full, which similarity
    search would neither preserve nor guarantee.
    """
    if context is None or not context.history:
        return []
    return [
        {"role": "assistant" if role == "assistant" else "user", "content": text}
        for role, text in context.history
    ]


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text == "" or text.lower() == "null" else text


class LlmIntentExtractor:
    def __init__(self, llm: _Completer, fallback: _Extractor) -> None:
        self._llm = llm
        self._fallback = fallback

    async def extract(
        self, question: str, context: "ChatContext | None" = None
    ) -> ExtractedIntent:
        if not question.strip():
            return await self._fallback.extract(question, context)
        try:
            text = await self._llm.complete(
                _SYSTEM_PROMPT,
                # Prior turns first, so "and what about last week?" has a subject.
                [*_history_messages(context), {"role": "user", "content": question}],
                max_tokens=300,
                temperature=0,
            )
            data = _parse_json(text)
            name = data.get("intent")
            if name not in ALLOWED_INTENTS:
                raise ValueError(f"unhandled intent: {name!r}")
            return ExtractedIntent(
                name=str(name),
                device_id=_str_or_none(data.get("device_id")),
                subsystem=_str_or_none(data.get("subsystem")),
                raw_question=question,
            )
        except Exception:  # noqa: BLE001 — deliberate: extractor must never raise into chat
            # Fail closed to the deterministic keyword classifier, which now also
            # resolves fragments from the same context.
            return await self._fallback.extract(question, context)
