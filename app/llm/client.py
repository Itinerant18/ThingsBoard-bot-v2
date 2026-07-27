from collections.abc import Iterable
from typing import Any, cast

from openai import AsyncOpenAI

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
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = await self.client.chat.completions.create(
            model=self._settings.openai_model,
            messages=cast(Iterable[Any], [{"role": "system", "content": system}, *messages]),
            max_tokens=max_tokens or self._settings.llm_max_tokens,
            **kwargs,
        )
        return response.choices[0].message.content or ""

    async def judge(self, question: str, answer: str) -> str:
        return await self.complete(
            "Evaluate the answer for factual grounding and concise usefulness.",
            [{"role": "user", "content": f"Question: {question}\nAnswer: {answer}"}],
            300,
        )
