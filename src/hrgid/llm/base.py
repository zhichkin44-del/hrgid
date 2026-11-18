"""Общий интерфейс моделей: запрос, ответ, вызов инструментов, ошибки, цена в рублях."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Tier = Literal["cheap", "main"]


class LLMError(Exception):
    """Ошибка провайдера. retryable — стоит повторить (таймаут, 429, 5xx)."""

    def __init__(self, message: str, *, retryable: bool = True, provider: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.provider = provider


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass(frozen=True)
class LLMRequest:
    system: str
    messages: tuple[Message, ...]
    tier: Tier = "cheap"
    tools: tuple[ToolSpec, ...] = ()
    max_tokens: int = 1024
    purpose: str = ""  # route | answer | support | calc — для трассы и записей


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    name: str

    def model_for(self, tier: Tier) -> str: ...

    def complete(self, req: LLMRequest) -> LLMResponse: ...


# Оценочные цены, ₽ за 1 млн токенов (вход, выход). СВЕРИТЬ С ПРАЙСОМ: цены claude-*-5-5 перенесены
# с прежних версий и не проверены по актуальному прайсу Anthropic. Обновлять по прайсам провайдеров.
RUB_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (90.0, 450.0),
    "claude-sonnet-5-5": (270.0, 1350.0),
    "GigaChat-2": (65.0, 65.0),
    "GigaChat-2-Pro": (500.0, 500.0),
    "yandexgpt-lite": (200.0, 200.0),
    "yandexgpt": (1200.0, 1200.0),
}


def cost_rub(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = RUB_PER_MTOK.get(model, (0.0, 0.0))
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


@dataclass
class UsageMeter:
    """Накопитель расходов одного запроса пользователя."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    calls: int = 0
    models: list[str] = field(default_factory=list)

    def add(self, r: LLMResponse) -> None:
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        self.cost += cost_rub(r.model, r.input_tokens, r.output_tokens)
        self.calls += 1
        if r.model not in self.models:
            self.models.append(r.model)
