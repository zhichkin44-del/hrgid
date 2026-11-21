"""Адаптер OpenRouter (OpenAI-совместимый API https://openrouter.ai/api/v1/chat/completions).

Ключ — OPENROUTER_API_KEY только из окружения; без него не вызывается. Инструменты — tool_calls.
Цены моделей берутся из открытого списка /models (USD за токен), в ₽ — по курсу HRGID_USD_RUB;
если список недоступен — запасная таблица. Идентификаторы сверены со списком 21.11.2026:
anthropic/claude-haiku-5.5 и anthropic/claude-sonnet-5.5.
OpenRouter — трансграничная передача данных, как и прямой Claude.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from hrgid.llm.base import (
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    Tier,
    ToolCall,
    register_price,
)

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"
MODELS_URL = f"{BASE_URL}/models"
MODELS: dict[Tier, str] = {  # бюджетный Haiku на все роли (route, answer, проверка цитат)
    "cheap": "anthropic/claude-haiku-5.5",
    "main": "anthropic/claude-haiku-5.5",  # решение владельца: Haiku для всех ролей
}
# USD за 1 токен (вход, выход) по списку моделей на 21.11.2026; если список недоступен.
FALLBACK_USD_PER_TOKEN: dict[str, tuple[float, float]] = {
    "anthropic/claude-haiku-5.5": (1e-7, 5e-7),
    "anthropic/claude-sonnet-5.5": (2e-6, 1e-5),
}
DEFAULT_USD_RUB = 95.0
APP_TITLE = "HRgid"
APP_REFERER = "https://github.com/zhichkin44-del/hrgid"


def usd_rub() -> float:
    try:
        v = float(os.environ.get("HRGID_USD_RUB", ""))
    except ValueError:
        return DEFAULT_USD_RUB
    return v if v > 0 else DEFAULT_USD_RUB


def to_openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(c.args, ensure_ascii=False),
                            },
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _retryable(status: int) -> bool:
    return status in (408, 409, 425, 429) or status >= 500


class OpenRouterClient:
    name = "openrouter"

    def __init__(
        self,
        http: httpx.Client | None = None,
        api_key: str | None = None,
        models: dict[Tier, str] | None = None,
        timeout: float = 30.0,
        rate: float | None = None,
    ) -> None:
        self._key = api_key
        self.models = models or MODELS
        self._http = http or httpx.Client(timeout=timeout)
        self._rate = rate
        self._priced: set[str] = set()
        self._catalog: dict[str, tuple[float, float]] | None = None

    def model_for(self, tier: Tier) -> str:
        return self.models[tier]

    def _headers(self) -> dict[str, str]:
        key = self._key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise LLMError("OPENROUTER_API_KEY не задан", retryable=False, provider=self.name)
        return {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": APP_REFERER,
            "X-Title": APP_TITLE,
            "Content-Type": "application/json",
        }

    def _load_catalog(self) -> dict[str, tuple[float, float]]:
        if self._catalog is None:
            cat: dict[str, tuple[float, float]] = {}
            try:
                r = self._http.get(MODELS_URL)
                r.raise_for_status()
                for m in r.json().get("data", []):
                    p = m.get("pricing") or {}
                    cat[str(m["id"])] = (float(p["prompt"]), float(p["completion"]))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                cat = {}
            self._catalog = cat
        return self._catalog

    def _price(self, model: str) -> None:
        """Записать цену модели (₽/млн токенов) в общую таблицу учёта стоимости."""
        if model in self._priced:
            return
        usd = self._load_catalog().get(model) or FALLBACK_USD_PER_TOKEN.get(model)
        if usd is not None:
            rate = self._rate if self._rate is not None else usd_rub()
            register_price(model, usd[0] * 1e6 * rate, usd[1] * 1e6 * rate)
        self._priced.add(model)

    def complete(self, req: LLMRequest) -> LLMResponse:
        model = self.model_for(req.tier)
        headers = self._headers()
        body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(req.system, req.messages),
            "max_tokens": req.max_tokens,
            "temperature": 0.1,
            # Haiku 5.5 на OpenRouter по умолчанию «размышляет»: размышление съедает max_tokens,
            # и ответ приходит пустым (замер 21.11.2026: 31 пустой ответ из 104). Отключаем.
            "reasoning": {"enabled": False},
        }
        if req.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.schema,
                    },
                }
                for t in req.tools
            ]
            body["tool_choice"] = "auto"
        try:
            r = self._http.post(CHAT_URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(str(e), retryable=True, provider=self.name) from e
        if r.status_code >= 400:
            raise LLMError(
                f"HTTP {r.status_code}", retryable=_retryable(r.status_code), provider=self.name
            )
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError("ответ не JSON", retryable=True, provider=self.name) from e
        if isinstance(data, dict) and data.get("error"):  # OpenRouter отдаёт ошибки и с кодом 200
            err = data["error"]
            code = int(err.get("code", 500)) if isinstance(err, dict) else 500
            raise LLMError(f"error {code}", retryable=_retryable(code), provider=self.name)
        try:
            msg = data["choices"][0]["message"]
            calls = tuple(
                ToolCall(
                    str(c["id"]),
                    str(c["function"]["name"]),
                    dict(json.loads(c["function"].get("arguments") or "{}")),
                )
                for c in (msg.get("tool_calls") or [])
            )
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"неожиданный ответ: {e}", retryable=True, provider=self.name) from e
        self._price(model)
        u = data.get("usage") or {}
        return LLMResponse(
            text=msg.get("content") or "",
            model=model,
            tool_calls=calls,
            input_tokens=int(u.get("prompt_tokens", 0)),
            output_tokens=int(u.get("completion_tokens", 0)),
        )
