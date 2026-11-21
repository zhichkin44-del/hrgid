from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hrgid.flow.build import PROVIDERS, make_client, provider_from_env
from hrgid.llm.base import (
    RUB_PER_MTOK,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    UsageMeter,
)
from hrgid.llm.fake import FakeLLM
from hrgid.llm.openrouter import MODELS, OpenRouterClient, to_openai_messages
from hrgid.llm.resilient import ResilientLLM

REQ = LLMRequest(system="s", messages=(Message("user", "привет"),), purpose="t")
OK = {
    "choices": [{"message": {"content": "ответ"}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
}
CATALOG = {
    "data": [
        {
            "id": "anthropic/claude-haiku-5.5",
            "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
        },
        {
            "id": "anthropic/claude-sonnet-5.5",
            "pricing": {"prompt": "0.000002", "completion": "0.00001"},
        },
    ]
}


def client(handler: Any, **kw: Any) -> OpenRouterClient:
    return OpenRouterClient(
        http=httpx.Client(transport=httpx.MockTransport(handler)), api_key="K", rate=100.0, **kw
    )


def router(chat: Any) -> Any:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/models"):
            return httpx.Response(200, json=CATALOG)
        return chat(r)  # type: ignore[no-any-return]

    return handler


def ok(r: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=OK)


def test_default_models_and_registration() -> None:
    assert set(MODELS.values()) == {"anthropic/claude-haiku-5.5"}  # Haiku for all roles
    assert "openrouter" in PROVIDERS and make_client("openrouter").name == "openrouter"


def test_request_shape_and_response() -> None:
    seen: dict[str, Any] = {}

    def chat(r: httpx.Request) -> httpx.Response:
        seen["url"] = str(r.url)
        seen["h"] = r.headers
        seen["body"] = json.loads(r.content)
        return httpx.Response(200, json=OK)

    resp = client(router(chat)).complete(REQ)
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["h"]["authorization"] == "Bearer K"
    assert seen["h"]["x-title"] == "HRgid" and "http-referer" in seen["h"]
    b = seen["body"]
    assert b["model"] == "anthropic/claude-haiku-5.5" and "tools" not in b
    assert b["reasoning"] == {"enabled": False}  # otherwise the reasoning eats max_tokens
    assert b["messages"][0] == {"role": "system", "content": "s"}
    assert resp.text == "ответ" and (resp.input_tokens, resp.output_tokens) == (1000, 200)
    main = client(router(chat)).complete(LLMRequest("s", REQ.messages, tier="main"))
    assert main.model == "anthropic/claude-haiku-5.5"


def test_tool_calling_roundtrip() -> None:
    tool = ToolSpec(
        "calc_r1", "расчёт", {"type": "object", "properties": {"n": {"type": "integer"}}}
    )
    bodies: list[dict[str, Any]] = []

    def chat(r: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(r.content))
        if len(bodies) == 1:
            fn = {"name": "calc_r1", "arguments": '{"n": 3}'}
            msg = {
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": fn}],
            }
            return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {}})
        return httpx.Response(200, json=OK)

    c = client(router(chat))
    r1 = c.complete(LLMRequest("s", REQ.messages, tools=(tool,)))
    assert r1.tool_calls == (ToolCall("c1", "calc_r1", {"n": 3}),)
    assert bodies[0]["tools"][0]["function"]["parameters"] == tool.schema
    assert bodies[0]["tool_choice"] == "auto"
    follow = (
        *REQ.messages,
        Message("assistant", "", r1.tool_calls),
        Message("tool", '{"days": 5}', tool_call_id="c1", tool_name="calc_r1"),
    )
    assert c.complete(LLMRequest("s", follow, tools=(tool,))).text == "ответ"
    sent = bodies[1]["messages"]
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == '{"n": 3}'
    assert sent[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"days": 5}'}


def test_message_conversion_keeps_all_parallel_calls() -> None:
    calls = (ToolCall("a", "f", {}), ToolCall("b", "g", {"x": 1}))
    out = to_openai_messages("s", (Message("assistant", "t", calls),))
    assert [c["id"] for c in out[1]["tool_calls"]] == ["a", "b"] and out[1]["content"] == "t"


def test_no_key_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def boom(r: httpx.Request) -> httpx.Response:
        raise AssertionError("сеть не должна вызываться")

    with pytest.raises(LLMError) as e:
        OpenRouterClient(http=httpx.Client(transport=httpx.MockTransport(boom))).complete(REQ)
    assert not e.value.retryable


def test_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "ENVKEY")
    auth: list[str] = []

    def chat(r: httpx.Request) -> httpx.Response:
        auth.append(r.headers["authorization"])
        return httpx.Response(200, json=OK)

    OpenRouterClient(http=httpx.Client(transport=httpx.MockTransport(router(chat)))).complete(REQ)
    assert auth == ["Bearer ENVKEY"]


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(401, False), (402, False), (400, False), (408, True), (429, True), (500, True), (503, True)],
)
def test_http_errors(status: int, retryable: bool) -> None:
    c = client(router(lambda r: httpx.Response(status, json={"error": {"message": "x"}})))
    with pytest.raises(LLMError) as e:
        c.complete(REQ)
    assert e.value.retryable is retryable and e.value.provider == "openrouter"


def test_error_inside_200_body_and_network_error() -> None:
    body = {"error": {"code": 429, "message": "limit"}}
    c = client(router(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(LLMError) as e:
        c.complete(REQ)
    assert e.value.retryable

    def down(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети")

    with pytest.raises(LLMError) as e2:
        client(down).complete(REQ)
    assert e2.value.retryable


def test_retries_on_429_then_success() -> None:
    n = {"i": 0}

    def chat(r: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(429) if n["i"] < 3 else httpx.Response(200, json=OK)

    sleeps: list[float] = []
    llm = ResilientLLM(client(router(chat)), retries=2, sleep=sleeps.append)
    assert llm.complete(REQ).text == "ответ" and n["i"] == 3 and len(sleeps) == 2


def test_5xx_falls_back_to_backup_model() -> None:
    backup_models = {"cheap": "anthropic/claude-sonnet-4.6", "main": "anthropic/claude-sonnet-4.6"}
    used: list[str] = []

    def chat(r: httpx.Request) -> httpx.Response:
        model = json.loads(r.content)["model"]
        used.append(model)
        return httpx.Response(503) if model.endswith("haiku-5.5") else httpx.Response(200, json=OK)

    primary = client(router(chat))
    backup = client(router(chat), models=backup_models)
    llm = ResilientLLM(primary, backup, retries=1, sleep=lambda s: None)
    r = llm.complete(REQ)
    assert r.text == "ответ" and r.model == "anthropic/claude-sonnet-4.6"
    assert used == ["anthropic/claude-haiku-5.5"] * 2 + ["anthropic/claude-sonnet-4.6"]
    assert llm.errors["openrouter"] == 2


def test_401_not_retried_but_fallback_used() -> None:
    n = {"i": 0}

    def chat(r: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(401)

    llm = ResilientLLM(client(router(chat)), FakeLLM(lambda r: "запас"), sleep=lambda s: None)
    assert llm.complete(REQ).text == "запас" and n["i"] == 1


def test_cost_from_model_list_prices() -> None:
    c = client(router(ok))
    r = c.complete(LLMRequest("s", REQ.messages, tier="main", purpose="answer"))
    # haiku-5.5: 1e-7 / 5e-7 USD per token, rate 100 -> 10 / 50 RUB per 1M tokens
    assert RUB_PER_MTOK["anthropic/claude-haiku-5.5"] == pytest.approx((10.0, 50.0))
    m = UsageMeter()
    m.add(r)
    assert m.cost == pytest.approx((1000 * 10.0 + 200 * 50.0) / 1e6) and m.models == [r.model]


def test_cost_falls_back_to_static_prices_when_catalog_down() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/models"):
            return httpx.Response(500)
        return httpx.Response(200, json=OK)

    r = client(handler).complete(REQ)
    m = UsageMeter()
    m.add(r)
    assert m.cost == pytest.approx((1000 * 10.0 + 200 * 50.0) / 1e6)


def test_usd_rub_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HRGID_USD_RUB", "80")
    http = httpx.Client(transport=httpx.MockTransport(router(ok)))
    OpenRouterClient(http=http, api_key="K").complete(REQ)
    assert RUB_PER_MTOK["anthropic/claude-haiku-5.5"][0] == pytest.approx(8.0)


def test_provider_env_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HRGID_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("HRGID_PROVIDER", raising=False)
    assert provider_from_env("fake") == "fake"
    monkeypatch.setenv("HRGID_PROVIDER", "claude")
    assert provider_from_env("fake") == "claude"
    monkeypatch.setenv("HRGID_LLM_PROVIDER", "openrouter")
    assert provider_from_env("fake") == "openrouter"


def test_response_type() -> None:
    assert isinstance(client(router(ok)).complete(REQ), LLMResponse)
