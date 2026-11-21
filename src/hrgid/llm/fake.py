"""Fake model for tests: responses are set by a handler function, and all requests are
remembered."""

from __future__ import annotations

import json
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


NO_MODEL_ANSWER = json.dumps({"no_answer": True, "answer": "", "claims": []})


def no_model_client() -> FakeLLM:
    """Run without keys (HRGID_LLM_PROVIDER=fake): "law" route; to a question it returns "ответа
    нет"."""

    def handler(req: LLMRequest) -> LLMResponse | str:
        return json.dumps({"route": "law"}) if req.purpose == "route" else NO_MODEL_ANSWER

    return FakeLLM(handler)
