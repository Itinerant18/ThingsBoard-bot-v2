"""SSE contract between /ask/stream and the chat widget.

frontend/src/context/ChatContext.tsx acts on exactly three event names — `token`,
`done`, `error` — and silently ignores anything else. So a rename here does not
surface as an error: the widget shows its typing indicator, receives a frame it
does not recognise, and settles with no bot bubble at all. Nothing appears in the
server log either, because the request succeeded.

These tests pin the names and the payload fields the widget reads, so that failure
mode turns into a red test instead of a blank chat window.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.chat import ChatRequest, ask_stream
from app.auth.jwt import TenantContext

TENANT = TenantContext(
    tenant_id="t-1",
    customer_id="c-1",
    subject="user@example.com",
    claims={},
    scopes=[],
    region="Kolkata",
    prefix="BOI",
    user_token="tok",
)


def _request(answer: Any = None, boom: bool = False) -> SimpleNamespace:
    class Orchestrator:
        async def ask(self, message: str, ctx: Any, session_id: str | None = None) -> Any:
            if boom:
                raise RuntimeError("orchestrator exploded")
            return answer

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(orchestrator=Orchestrator(), tb=None))
    )


async def _frames(response: Any) -> list[dict[str, str]]:
    """Collect the frames the endpoint yields.

    sse_starlette's body_iterator yields the {event, data} dicts before they are
    serialised to the wire, which is the level worth asserting on — the encoding
    itself is the library's responsibility, not this contract's.
    """
    return [frame async for frame in response.body_iterator]


async def _run(request: Any) -> list[dict[str, str]]:
    response = await ask_stream(
        request=request,  # type: ignore[arg-type]
        payload=ChatRequest(message="how many devices"),
        tenant=TENANT,
        db=None,  # type: ignore[arg-type]
        redis=None,  # type: ignore[arg-type]
    )
    return await _frames(response)


@pytest.mark.asyncio
async def test_success_emits_a_done_frame_the_widget_understands() -> None:
    answer = SimpleNamespace(
        text="You have 104 devices.", structured={"count": 104}, sources=["hierarchy"], used_llm=False
    )
    frames = await _run(_request(answer=answer))

    assert len(frames) == 1
    # The widget finalizes on `done`; any other name renders an empty bubble.
    assert frames[0]["event"] == "done"

    payload = json.loads(frames[0]["data"])
    assert payload["answer"] == "You have 104 devices."
    # Both read explicitly by the widget's finalizeMessage.
    assert payload["error"] is False
    assert isinstance(payload["timestamp"], int)


@pytest.mark.asyncio
async def test_orchestrator_failure_becomes_an_in_band_error_frame() -> None:
    """The stream is already committed when the failure happens, so it cannot become
    an HTTP error status — it has to arrive as an `error` frame or the widget hangs
    on its typing indicator until the socket closes."""
    frames = await _run(_request(boom=True))

    assert len(frames) == 1
    assert frames[0]["event"] == "error"
    assert json.loads(frames[0]["data"])["errorMessage"]


@pytest.mark.asyncio
async def test_no_frame_uses_an_event_name_the_widget_ignores() -> None:
    answer = SimpleNamespace(text="ok", structured={}, sources=[], used_llm=False)
    understood = {"token", "done", "error"}
    for request in (_request(answer=answer), _request(boom=True)):
        for frame in await _run(request):
            assert frame["event"] in understood, frame["event"]
