"""Через настоящий протокол MCP (клиент-сессия в памяти): список инструментов и вызовы."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from hrgid.mcp.server import TOKEN_ENV, build_server, env_token

from .conftest import A, Rig

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)


def _session(rig: Rig) -> Any:
    return create_connected_server_and_client_session(build_server(rig.gw, env_token))


def _text(res: Any) -> str:
    return str(res.content[0].text)


async def test_list_tools_read_only_with_schemas(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, rig.tok[A])
    async with _session(rig) as client:
        tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == ["search_law", "search_policy", "calc", "check_policy_clause"]
    assert all(t.annotations and t.annotations.readOnlyHint for t in tools)
    calc = next(t for t in tools if t.name == "calc")
    assert len(calc.inputSchema["oneOf"]) == 9


async def test_call_search_law_over_protocol(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, rig.tok[A])
    async with _session(rig) as client:
        res = await client.call_tool("search_law", {"query": "основной отпуск сколько дней"})
    assert not res.isError
    assert json.loads(_text(res))["results"][0]["ref"] == "ТК РФ, ст. 115"


async def test_call_calc_over_protocol(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, rig.tok[A])
    async with _session(rig) as client:
        res = await client.call_tool(
            "calc", {"kind": "probation_end", "args": {"start": "2026-11-02", "months": 3}}
        )
        bad = await client.call_tool("calc", {"kind": "probation_end"})  # args обязателен
    assert not res.isError and "R9" in _text(res)
    assert bad.isError


async def test_no_token_and_revoked_token_are_refused(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _session(rig) as client:  # токена в окружении нет
        res = await client.call_tool("search_law", {"query": "отпуск"})
    assert res.isError and "недействителен" in _text(res)
    monkeypatch.setenv(TOKEN_ENV, rig.tok[A])
    async with _session(rig) as client:
        ok = await client.call_tool("search_law", {"query": "отпуск"})
        rig.tokens.revoke(A, rig.tokens.list(A)[0]["id"], "admin")  # отзыв посреди сессии
        denied = await client.call_tool("search_law", {"query": "отпуск"})
    assert not ok.isError and denied.isError
