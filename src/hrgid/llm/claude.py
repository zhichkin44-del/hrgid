"""Адаптер Claude (Anthropic SDK). Ключ — ANTHROPIC_API_KEY из окружения; без него не вызывается."""

from __future__ import annotations

import os
from typing import Any

import anthropic

from hrgid.llm.base import LLMError, LLMRequest, LLMResponse, Message, Tier, ToolCall

MODELS: dict[Tier, str] = {"cheap": "claude-haiku-4-5", "main": "claude-sonnet-4-5"}


def to_anthropic_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                for c in m.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


class ClaudeClient:
    name = "claude"

    def __init__(
        self,
        client: Any | None = None,
        models: dict[Tier, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = client
        self.models = models or MODELS
        self.timeout = timeout

    def _api(self) -> Any:
        if self._client is None:
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise LLMError("ANTHROPIC_API_KEY не задан", retryable=False, provider=self.name)
            self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout, max_retries=0)
        return self._client

    def model_for(self, tier: Tier) -> str:
        return self.models[tier]

    def complete(self, req: LLMRequest) -> LLMResponse:
        model = self.model_for(req.tier)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": to_anthropic_messages(req.messages),
        }
        if req.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.schema}
                for t in req.tools
            ]
        api = self._api()
        try:
            resp = api.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            retry = e.status_code in (408, 409, 429) or e.status_code >= 500
            raise LLMError(str(e), retryable=retry, provider=self.name) from e
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            raise LLMError(str(e), retryable=True, provider=self.name) from e
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = tuple(
            ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"
        )
        return LLMResponse(
            text=text,
            model=model,
            tool_calls=calls,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
