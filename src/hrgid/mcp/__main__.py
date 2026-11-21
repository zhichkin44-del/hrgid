"""Starts the HRgid MCP server.

    HRGID_MCP_TOKEN=hrg_... python -m hrgid.mcp            # stdio (Claude Code, Claude Desktop)
    # streamable HTTP, token in Authorization
    python -m hrgid.mcp --http --port 8765

Requires DATABASE_URL (otherwise the bundled pgserver) and, for check_policy_clause, a model key
(HRGID_LLM_PROVIDER, default claude). The company is determined by the token from the panel (П9).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

import hrgid  # noqa: F401  # first: prepares libpq on Windows

from hrgid.bot.store import PgDocStore  # isort: skip
from hrgid.mcp.clause import LlmClauseChecker
from hrgid.mcp.gateway import McpGateway
from hrgid.mcp.server import build_server, env_token, header_token
from hrgid.mcp.tokens import McpTokens


def build_gateway_from_env() -> McpGateway:
    from hrgid.db import connect, database_url, init_schema
    from hrgid.docs.tkcheck import HybridNormSource
    from hrgid.flow.build import make_client, make_searcher, provider_from_env
    from hrgid.llm.resilient import ResilientLLM

    conn = connect(database_url())
    init_schema(conn)
    store = PgDocStore(conn)
    store.init_schema()
    searcher = make_searcher(conn, os.environ.get("HRGID_RERANKER") or "mmarco-minilm")
    clause = None
    try:
        llm = ResilientLLM(make_client(provider_from_env("claude")))
        clause = LlmClauseChecker(llm, HybridNormSource(conn, searcher.embedder, searcher.reranker))
    except Exception:  # no model key: search and calculations work, the item check does not
        clause = None
    return McpGateway(McpTokens(store), searcher, clause=clause)


def http_app(gw: McpGateway) -> Any:
    """ASGI application: /mcp behind the Bearer token check (401 immediately, before the MCP
    protocol)."""
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    server = build_server(gw, header_token)
    manager = StreamableHTTPSessionManager(server, stateless=True)

    class Guard:
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode("latin-1")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
            if gw.tokens.verify(token) is None:
                resp = JSONResponse({"error": "Токен недействителен или отозван."}, status_code=401)
                await resp(scope, receive, send)
                return
            await manager.handle_request(scope, receive, send)

    import contextlib

    @contextlib.asynccontextmanager
    async def lifespan(_: Any) -> Any:
        async with manager.run():
            yield

    return Starlette(
        routes=[Route("/mcp", Guard(), methods=["GET", "POST", "DELETE"])], lifespan=lifespan
    )


async def run_stdio(gw: McpGateway) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(gw, env_token)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m hrgid.mcp")
    ap.add_argument("--http", action="store_true", help="streamable HTTP вместо stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    gw = build_gateway_from_env()
    if a.http:
        import uvicorn

        uvicorn.run(http_app(gw), host=a.host, port=a.port)
    else:
        asyncio.run(run_stdio(gw))


if __name__ == "__main__":
    main()
