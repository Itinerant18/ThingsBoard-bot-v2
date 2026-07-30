from collections.abc import Iterable
from typing import Any, cast

from openai import AsyncOpenAI, BadRequestError

from app.config import Settings


class LlmClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if not self._settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for LLM-powered requests")
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        return self._client

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        # gpt-5.x rejects `max_tokens` ("Unsupported parameter ... Use
        # 'max_completion_tokens' instead") and rejects any temperature other than the
        # default. Sending either produced a 400 on EVERY call — and because
        # LlmIntentExtractor catches all exceptions to stay fail-closed, the LLM would
        # have looked enabled while silently never running. Try the modern parameter
        # first and fall back for older models, rather than pinning to one generation.
        limit = max_tokens or self._settings.llm_max_tokens
        base: dict[str, Any] = {
            "model": self._settings.openai_model,
            "messages": cast(
                Iterable[Any], [{"role": "system", "content": system}, *messages]
            ),
        }
        try:
            response = await self.client.chat.completions.create(
                **base, max_completion_tokens=limit
            )
        except BadRequestError:
            kwargs: dict[str, Any] = {}
            if temperature is not None:
                kwargs["temperature"] = temperature
            response = await self.client.chat.completions.create(
                **base, max_tokens=limit, **kwargs
            )
        return response.choices[0].message.content or ""

    async def judge(self, question: str, answer: str) -> str:
        return await self.complete(
            "Evaluate the answer for factual grounding and concise usefulness.",
            [{"role": "user", "content": f"Question: {question}\nAnswer: {answer}"}],
            300,
        )
