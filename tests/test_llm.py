from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from hrgid.llm.base import (
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    UsageMeter,
    cost_rub,
)
from hrgid.llm.claude import ClaudeClient, to_anthropic_messages
from hrgid.llm.fake import FakeLLM
from hrgid.llm.gigachat import GigaChatClient
from hrgid.llm.recordings import MissingRecording, RecordingLLM
from hrgid.llm.resilient import ResilientLLM
from hrgid.llm.yandex import YandexGPTClient

REQ = LLMRequest(system="s", messages=(Message("user", "привет"),), purpose="t")


class Flaky:
    name = "flaky"

    def __init__(self, fails: int, retryable: bool = True) -> None:
        self.fails = fails
        self.retryable = retryable
        self.calls = 0

    def model_for(self, tier: str) -> str:
        return "flaky"

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls <= self.fails:
            raise LLMError("boom", retryable=self.retryable, provider=self.name)
        return LLMResponse("ok", "flaky")


def test_retry_then_success() -> None:
    c = Flaky(2)
    sleeps: list[float] = []
    r = ResilientLLM(c, retries=2, sleep=sleeps.append)
    assert r.complete(REQ).text == "ok"
    assert c.calls == 3 and len(sleeps) == 2


def test_fallback_after_retries() -> None:
    primary = Flaky(99)
    fb = FakeLLM(lambda r: "запасная")
    r = ResilientLLM(primary, fb, retries=2, sleep=lambda s: None)
    assert r.complete(REQ).text == "запасная"
    assert primary.calls == 3 and r.errors["flaky"] == 3


def test_no_retry_when_not_retryable_and_raises() -> None:
    c = Flaky(99, retryable=False)
    r = ResilientLLM(c, retries=2, sleep=lambda s: None)
    with pytest.raises(LLMError):
        r.complete(REQ)
    assert c.calls == 1


def test_claude_without_key_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError) as e:
        ClaudeClient().complete(REQ)
    assert not e.value.retryable


def test_claude_message_conversion_merges_tool_results() -> None:
    call = ToolCall("c1", "f", {"a": 1})
    msgs = (
        Message("user", "q"),
        Message("assistant", "", (call, ToolCall("c2", "g", {}))),
        Message("tool", "r1", tool_call_id="c1", tool_name="f"),
        Message("tool", "r2", tool_call_id="c2", tool_name="g"),
    )
    out = to_anthropic_messages(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert len(out[2]["content"]) == 2


def test_claude_parses_response() -> None:
    class Block:
        def __init__(self, **kw: Any) -> None:
            self.__dict__.update(kw)

    class Api:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw: Any) -> Any:
                assert kw["model"] == "claude-sonnet-5-5"
                assert kw["tools"][0]["name"] == "t"
                return Block(
                    content=[
                        Block(type="text", text="привет"),
                        Block(type="tool_use", id="x", name="t", input={"k": 1}),
                    ],
                    usage=Block(input_tokens=10, output_tokens=5),
                )

    tool = ToolSpec("t", "d", {"type": "object"})
    req = LLMRequest("s", (Message("user", "q"),), tier="main", tools=(tool,))
    r = ClaudeClient(client=Api()).complete(req)
    assert r.text == "привет" and r.tool_calls[0].args == {"k": 1}
    assert (r.input_tokens, r.output_tokens) == (10, 5)


def test_gigachat_oauth_and_chat() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "oauth" in str(request.url):
            assert request.headers["Authorization"] == "Basic KEY"
            return httpx.Response(200, json={"access_token": "T", "expires_at": 9_999_999_999_999})
        assert request.headers["Authorization"] == "Bearer T"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "function_call": {"name": "f", "arguments": {"x": 2}},
                        }
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    c = GigaChatClient(http=http, credentials="KEY")
    tool = ToolSpec("f", "d", {"type": "object"})
    r = c.complete(LLMRequest("s", (Message("user", "q"),), tools=(tool,)))
    assert r.tool_calls[0].name == "f" and r.tool_calls[0].args == {"x": 2}
    c.complete(REQ)
    assert sum("oauth" in u for u in seen) == 1  # токен переиспользуется


def test_gigachat_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    with pytest.raises(LLMError):
        GigaChatClient(http=httpx.Client(transport=httpx.MockTransport(lambda r: None))).complete(  # type: ignore[arg-type]
            REQ
        )


def test_gigachat_5xx_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url):
            return httpx.Response(200, json={"access_token": "T", "expires_at": 9_999_999_999_999})
        return httpx.Response(503)

    c = GigaChatClient(http=httpx.Client(transport=httpx.MockTransport(handler)), credentials="K")
    with pytest.raises(LLMError) as e:
        c.complete(REQ)
    assert e.value.retryable


def test_yandex_request_and_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Api-Key K"
        assert request.headers["x-folder-id"] == "F"
        body = request.read().decode()
        assert "gpt://F/yandexgpt-lite/latest" in body
        return httpx.Response(
            200,
            json={
                "result": {
                    "alternatives": [{"message": {"role": "assistant", "text": "да"}}],
                    "usage": {"inputTextTokens": "12", "completionTokens": "1"},
                }
            },
        )

    c = YandexGPTClient(
        http=httpx.Client(transport=httpx.MockTransport(handler)), api_key="K", folder_id="F"
    )
    r = c.complete(REQ)
    assert r.text == "да" and r.input_tokens == 12


def test_yandex_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    with pytest.raises(LLMError):
        YandexGPTClient().complete(REQ)


def test_cost_and_meter() -> None:
    assert cost_rub("claude-haiku-5-5", 1_000_000, 0) == 90.0
    m = UsageMeter()
    m.add(LLMResponse("x", "claude-haiku-5-5", input_tokens=1000, output_tokens=100))
    assert m.calls == 1 and m.cost > 0


def test_recording_replay_and_prompt_change_fails(tmp_path: Path) -> None:
    inner = FakeLLM(lambda r: "записано")
    rec = RecordingLLM(inner, "v1", tmp_path, record=True)
    assert rec.complete(REQ).text == "записано"
    replay = RecordingLLM(None, "v1", tmp_path, model_name="fake")
    # ключ зависит от имени модели: у Fake model_for = fake-cheap, у replay — fake-cheap
    assert replay.complete(REQ).text == "записано"
    changed = LLMRequest(system="другой промпт", messages=REQ.messages, purpose="t")
    with pytest.raises(MissingRecording):
        replay.complete(changed)
    with pytest.raises(MissingRecording):  # другая версия набора
        RecordingLLM(None, "v2", tmp_path, model_name="fake").complete(REQ)
