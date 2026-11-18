"""Адаптер YandexGPT (Foundation Models API, https://yandex.cloud/ru/docs/foundation-models/).

POST llm.api.cloud.yandex.net/foundationModels/v1/completion, заголовки
Authorization: Api-Key <ключ> и x-folder-id. Модель задаётся modelUri gpt://<каталог>/<модель>/latest.
Инструменты — поле tools, ответ с toolCallList. Ключи — YANDEX_API_KEY, YANDEX_FOLDER_ID;
без них не вызывается.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx

from hrgid.llm.base import LLMError, LLMRequest, LLMResponse, Message, Tier, ToolCall

URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
MODELS: dict[Tier, str] = {"cheap": "yandexgpt-lite", "main": "yandexgpt"}


def to_yandex_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "text": system}]
    for m in messages:
        if m.role == "tool":
            res = {"functionResult": {"name": m.tool_name, "content": m.content}}
            out.append({"role": "user", "toolResultList": {"toolResults": [res]}})
        elif m.role == "assistant" and m.tool_calls:
            calls = [{"functionCall": {"name": c.name, "arguments": c.args}} for c in m.tool_calls]
            out.append({"role": "assistant", "toolCallList": {"toolCalls": calls}})
        else:
            out.append({"role": m.role, "text": m.content})
    return out


class YandexGPTClient:
    name = "yandexgpt"

    def __init__(
        self,
        http: httpx.Client | None = None,
        api_key: str | None = None,
        folder_id: str | None = None,
        models: dict[Tier, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._key = api_key
        self._folder = folder_id
        self.models = models or MODELS
        self._http = http or httpx.Client(timeout=timeout)

    def model_for(self, tier: Tier) -> str:
        return self.models[tier]

    def complete(self, req: LLMRequest) -> LLMResponse:
        key = self._key or os.environ.get("YANDEX_API_KEY")
        folder = self._folder or os.environ.get("YANDEX_FOLDER_ID")
        if not key or not folder:
            raise LLMError(
                "YANDEX_API_KEY/YANDEX_FOLDER_ID не заданы", retryable=False, provider=self.name
            )
        model = self.model_for(req.tier)
        body: dict[str, Any] = {
            "modelUri": f"gpt://{folder}/{model}/latest",
            "completionOptions": {
                "stream": False,
                "temperature": 0.1,
                "maxTokens": str(req.max_tokens),
            },
            "messages": to_yandex_messages(req.system, req.messages),
        }
        if req.tools:
            body["tools"] = [
                {"function": {"name": t.name, "description": t.description, "parameters": t.schema}}
                for t in req.tools
            ]
        try:
            r = self._http.post(
                URL, json=body, headers={"Authorization": f"Api-Key {key}", "x-folder-id": folder}
            )
        except httpx.HTTPError as e:
            raise LLMError(str(e), retryable=True, provider=self.name) from e
        if r.status_code >= 400:
            raise LLMError(
                f"HTTP {r.status_code}",
                retryable=r.status_code in (408, 429) or r.status_code >= 500,
                provider=self.name,
            )
        res = r.json()["result"]
        msg = res["alternatives"][0]["message"]
        calls: list[ToolCall] = []
        for c in (msg.get("toolCallList") or {}).get("toolCalls", []):
            fc = c["functionCall"]
            args = fc.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args or "{}")
            calls.append(ToolCall(str(uuid.uuid4()), fc["name"], dict(args)))
        u = res.get("usage", {})
        return LLMResponse(
            text=msg.get("text", ""),
            model=model,
            tool_calls=tuple(calls),
            input_tokens=int(u.get("inputTextTokens", 0)),
            output_tokens=int(u.get("completionTokens", 0)),
        )
