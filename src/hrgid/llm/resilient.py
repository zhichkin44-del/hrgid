"""Resilience (§5 item 6): retries with a pause, a fallback model, an error counter per provider."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable

from hrgid.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse, Tier

USER_MESSAGE = "Сервис ответов временно недоступен. Попробуйте ещё раз или передайте вопрос HR."


class ResilientLLM:
    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient | None = None,
        retries: int = 2,
        pause: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.retries = retries
        self.pause = pause
        self.sleep = sleep
        self.errors: Counter[str] = Counter()
        self.name = primary.name

    def model_for(self, tier: Tier) -> str:
        return self.primary.model_for(tier)

    def _try(self, client: LLMClient, req: LLMRequest) -> LLMResponse:
        last: LLMError | None = None
        for attempt in range(self.retries + 1):
            try:
                return client.complete(req)
            except LLMError as e:
                self.errors[client.name] += 1
                last = e
                if not e.retryable:
                    break
                if attempt < self.retries:
                    self.sleep(self.pause * (attempt + 1))
        assert last is not None
        raise last

    def complete(self, req: LLMRequest) -> LLMResponse:
        try:
            return self._try(self.primary, req)
        except LLMError as first:
            if self.fallback is None:
                raise
            try:
                return self._try(self.fallback, req)
            except LLMError:
                raise first from None
