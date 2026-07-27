"""LLM-backed intent extraction — port of Java LlmIntentExtractor's contract.

One deterministic (temperature 0) OpenAI call classifies the question into a
handled intent plus optional entities, parsed fail-closed: ANY failure — no API
key, API error, malformed JSON, or an intent outside the allowed set — falls back
to the deterministic keyword extractor. The extractor never raises into the chat
pipeline.
"""

import json
import re
from typing import Protocol

from app.query.contracts import ExtractedIntent

# Only intents the orchestrator has a handler for. Widen alongside new handlers;
# the LLM must not emit an intent that dead-ends at "could not map".
ALLOWED_INTENTS = (
    "global_overview",
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
    "You classify a facility-monitoring question into exactly one intent and "
    "extract entities. Reply with ONLY a JSON object, no prose, no code fences.\n"
    'Schema: {"intent": <one of: '
    + ", ".join(ALLOWED_INTENTS)
    + '>, "device_id": <device/asset id string or null>, '
    '"subsystem": <cctv|ias|bas|fas|timeLock|accessControl or null>}\n'
    "Guidance: global_overview=fleet-wide health/counts; device_inventory=list devices; "
    "alarm_detail=alarms/alerts; the *_status/*_voltage/battery_*/ac_voltage/system_current/"
    "power_status/cctv_*/device_hardware intents are single-device metric questions and need a "
    "device_id; subsystem_status=state of a named subsystem on one device."
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
    async def extract(self, question: str) -> ExtractedIntent: ...


def _parse_json(text: str) -> dict[str, object]:
    stripped = text.strip()
    # Tolerate ```json fenced or prose-wrapped output: take the first {...} block.
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    data = json.loads(match.group(0) if match else stripped)
    if not isinstance(data, dict):
        raise ValueError("intent JSON was not an object")  # noqa: TRY004 (caught, fail-closed)
    return data


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text == "" or text.lower() == "null" else text


class LlmIntentExtractor:
    def __init__(self, llm: _Completer, fallback: _Extractor) -> None:
        self._llm = llm
        self._fallback = fallback

    async def extract(self, question: str) -> ExtractedIntent:
        if not question.strip():
            return await self._fallback.extract(question)
        try:
            text = await self._llm.complete(
                _SYSTEM_PROMPT,
                [{"role": "user", "content": f"Extract the intent from:\n{question}"}],
                max_tokens=200,
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
            # Fail closed to the deterministic keyword classifier.
            return await self._fallback.extract(question)
