"""GigaChat adapter (REST, https://developers.sber.ru/docs/ru/gigachat/api/overview).

OAuth: POST ngw.devices.sberbank.ru:9443/api/v2/oauth, header Basic <authorization key>,
RqUID, scope GIGACHAT_API_PERS -> access_token for 30 minutes. Chat: /api/v1/chat/completions,
tools through functions/function_call. Ministry certificates are attached through ca_bundle.
Key: GIGACHAT_CREDENTIALS from the environment; without it, it is not called.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import httpx

from hrgid.llm.base import LLMError, LLMRequest, LLMResponse, Message, Tier, ToolCall

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
MODELS: dict[Tier, str] = {"cheap": "GigaChat-2", "main": "GigaChat-2-Pro"}


def to_giga_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "tool":
            out.append({"role": "function", "name": m.tool_name, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            c = m.tool_calls[0]
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or "",
                    "function_call": {"name": c.name, "arguments": c.args},
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


class GigaChatClient:
    name = "gigachat"

    def __init__(
        self,
        http: httpx.Client | None = None,
        credentials: str | None = None,
        scope: str = "GIGACHAT_API_PERS",
        models: dict[Tier, str] | None = None,
        timeout: float = 30.0,
        ca_bundle: str | None = None,
    ) -> None:
        self._credentials = credentials
        self.scope = scope
        self.models = models or MODELS
        self._http = http or httpx.Client(timeout=timeout, verify=ca_bundle or True)
        self._token = ""
        self._expires = 0.0

    def model_for(self, tier: Tier) -> str:
        return self.models[tier]

    def _auth(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        cred = self._credentials or os.environ.get("GIGACHAT_CREDENTIALS")
        if not cred:
            raise LLMError("GIGACHAT_CREDENTIALS не задан", retryable=False, provider=self.name)
        try:
            r = self._http.post(
                OAUTH_URL,
                headers={
                    "Authorization": f"Basic {cred}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"scope": self.scope},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"oauth: {e}", retryable=True, provider=self.name) from e
        data = r.json()
        self._token = str(data["access_token"])
        self._expires = float(data.get("expires_at", 0)) / 1000.0
        return self._token

    def complete(self, req: LLMRequest) -> LLMResponse:
        model = self.model_for(req.tier)
        body: dict[str, Any] = {
            "model": model,
            "messages": to_giga_messages(req.system, req.messages),
            "max_tokens": req.max_tokens,
            "temperature": 0.1,
        }
        if req.tools:
            body["functions"] = [
                {"name": t.name, "description": t.description, "parameters": t.schema}
                for t in req.tools
            ]
            body["function_call"] = "auto"
        token = self._auth()
        try:
            r = self._http.post(
                CHAT_URL,
                json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise LLMError(str(e), retryable=True, provider=self.name) from e
        if r.status_code >= 400:
            if r.status_code == 401:
                self._token = ""
            raise LLMError(
                f"HTTP {r.status_code}",
                retryable=r.status_code in (401, 408, 429) or r.status_code >= 500,
                provider=self.name,
            )
        data = r.json()
        msg = data["choices"][0]["message"]
        calls: tuple[ToolCall, ...] = ()
        fc = msg.get("function_call")
        if fc:
            args = fc.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args or "{}")
            calls = (ToolCall(str(uuid.uuid4()), fc["name"], dict(args)),)
        u = data.get("usage", {})
        return LLMResponse(
            text=msg.get("content") or "",
            model=model,
            tool_calls=calls,
            input_tokens=int(u.get("prompt_tokens", 0)),
            output_tokens=int(u.get("completion_tokens", 0)),
        )
