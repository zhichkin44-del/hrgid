"""Fake-модель для тестов: ответы задаёт функция-обработчик, все запросы запоминаются."""

from __future__ import annotations

from collections.abc import Callable

from hrgid.llm.base import LLMRequest, LLMResponse, Tier

Handler = Callable[[LLMRequest], LLMResponse | str]


class FakeLLM:
    name = "fake"

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[LLMRequest] = []

    def model_for(self, tier: Tier) -> str:
        return f"fake-{tier}"

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        out = self.handler(req)
        if isinstance(out, str):
            return LLMResponse(
                text=out, model=self.model_for(req.tier),
                input_tokens=len(req.system) // 4, output_tokens=len(out) // 4,
            )  # fmt: skip
        return out
