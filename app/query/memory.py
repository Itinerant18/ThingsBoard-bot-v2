"""Conversational memory — port of Java ChatMemoryService, Redis-backed.

Java kept a 4-message sliding window + the "active branch" per session in JVM maps
with a 30-minute idle sweep. v2 stores the same in Redis: EXPIRE replaces the sweep,
and state survives process restarts. The active branch is what makes follow-ups work:
"battery voltage of Liluah" ... "and its cctv status?" — the second question has no
branch name, so the orchestrator falls back to the remembered one.

SECURITY: callers must build the session key with `session_key()`, which binds it to
the verified JWT tenant + subject — a client-chosen conversation_id alone would let
one user read another's conversation context.

Memory must never break chat: every operation swallows Redis errors (fail-open on
context, the answer itself still goes through the scope gates).
"""

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_PREFIX = "chatmem:v1"
SESSION_TTL_SECONDS = 30 * 60  # Java SESSION_TTL_MS
MAX_HISTORY_MESSAGES = 4  # Java: 2 Q&A pairs


def session_key(tenant_id: str, subject: str, conversation_id: str) -> str:
    return f"{tenant_id}:{subject}:{conversation_id}"


def _hist_key(session: str) -> str:
    return f"{_PREFIX}:{session}:hist"


def _intent_key(session: str) -> str:
    return f"{_PREFIX}:{session}:intent"


def _branch_key(session: str) -> str:
    return f"{_PREFIX}:{session}:branch"


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


@dataclass(frozen=True)
class ChatContext:
    history: tuple[tuple[str, str], ...] = ()  # (role, text), oldest -> newest
    device_id: str | None = None  # active branch device
    branch_name: str | None = None
    intent: str | None = None  # last resolved intent, for fragment follow-ups


async def load_context(redis: "Redis", session: str) -> ChatContext:
    try:
        raw_history = await redis.lrange(_hist_key(session), 0, -1)
        raw_branch = await redis.get(_branch_key(session))
        raw_intent = await redis.get(_intent_key(session))
    except Exception:
        logger.warning("chat memory read failed for session", exc_info=True)
        return ChatContext()
    history: list[tuple[str, str]] = []
    for item in raw_history or []:
        try:
            entry = json.loads(_text(item))
            history.append((str(entry["role"]), str(entry["text"])))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    device_id: str | None = None
    branch_name: str | None = None
    if raw_branch:
        try:
            branch = json.loads(_text(raw_branch))
            device_id = branch.get("device_id") or None
            branch_name = branch.get("branch_name") or None
        except (json.JSONDecodeError, AttributeError):
            pass
    return ChatContext(
        history=tuple(history),
        device_id=device_id,
        branch_name=branch_name,
        intent=_text(raw_intent) if raw_intent else None,
    )


async def record_turn(redis: "Redis", session: str, question: str, answer_text: str) -> None:
    """Append one Q&A pair, trim to the sliding window, refresh the idle TTL."""
    key = _hist_key(session)
    try:
        await redis.rpush(
            key,
            json.dumps({"role": "user", "text": question}),
            json.dumps({"role": "assistant", "text": answer_text}),
        )
        await redis.ltrim(key, -MAX_HISTORY_MESSAGES, -1)
        await redis.expire(key, SESSION_TTL_SECONDS)
    except Exception:
        logger.warning("chat memory write failed for session", exc_info=True)


async def set_active_branch(
    redis: "Redis", session: str, device_id: str, branch_name: str | None
) -> None:
    try:
        await redis.set(
            _branch_key(session),
            json.dumps({"device_id": device_id, "branch_name": branch_name}),
            ex=SESSION_TTL_SECONDS,
        )
    except Exception:
        logger.warning("chat memory branch write failed for session", exc_info=True)


async def set_active_intent(redis: "Redis", session: str, intent: str) -> None:
    """Remember the last resolved intent so a fragment can inherit it.

    "and last week?" carries no intent words of its own; without this the keyword
    classifier falls through to its global_overview default and answers a question
    the user did not ask.
    """
    try:
        await redis.set(_intent_key(session), intent, ex=SESSION_TTL_SECONDS)
    except Exception:
        logger.warning("chat memory intent write failed for session", exc_info=True)


async def clear(redis: "Redis", session: str) -> None:
    try:
        await redis.delete(_hist_key(session), _branch_key(session), _intent_key(session))
    except Exception:
        logger.warning("chat memory clear failed for session", exc_info=True)
