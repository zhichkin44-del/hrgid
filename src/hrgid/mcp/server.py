"""Сервер MCP на официальном SDK (mcp): stdio и streamable HTTP. Все инструменты — только чтение."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server

from hrgid.mcp.gateway import TOOLS, McpGateway

TOKEN_ENV = "HRGID_MCP_TOKEN"

TokenFor = Callable[["Server[Any, Any]"], str | None]


def build_server(gw: McpGateway, token_for: TokenFor) -> Server[Any, Any]:
    """token_for достаёт токен текущего вызова: из окружения (stdio) или из заголовка (HTTP).

    Токен проверяется на каждом вызове: отзыв действует сразу, без перезапуска сервера.
    """
    server: Server[Any, Any] = Server("hrgid")

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list() -> list[types.Tool]:
        return [
            types.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.schema,
                annotations=types.ToolAnnotations(
                    readOnlyHint=True, destructiveHint=False, openWorldHint=False
                ),
            )
            for t in TOOLS
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        out = gw.call(token_for(server), name, arguments)  # исключение -> isError у клиента
        return [types.TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=1))]

    return server


def env_token(_: Server[Any, Any]) -> str | None:
    return os.environ.get(TOKEN_ENV) or None


def header_token(server: Server[Any, Any]) -> str | None:
    req = server.request_context.request
    auth = req.headers.get("authorization", "") if req is not None else ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None
