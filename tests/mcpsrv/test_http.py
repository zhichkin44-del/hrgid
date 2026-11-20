"""HTTP-вариант: токен в Authorization, чужой/отозванный — 401 до протокола MCP."""

from __future__ import annotations

from starlette.testclient import TestClient

from hrgid.mcp.__main__ import http_app

from .conftest import A, Rig

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_http_rejects_missing_wrong_and_revoked(rig: Rig) -> None:
    with TestClient(http_app(rig.gw)) as c:
        assert c.post("/mcp", json=INIT, headers=HDR).status_code == 401
        bad = {**HDR, "Authorization": "Bearer hrg_x"}
        assert c.post("/mcp", json=INIT, headers=bad).status_code == 401
        good = {**HDR, "Authorization": f"Bearer {rig.tok[A]}"}
        assert c.post("/mcp", json=INIT, headers=good).status_code == 200
        rig.tokens.revoke(A, rig.tokens.list(A)[0]["id"], "admin")
        assert c.post("/mcp", json=INIT, headers=good).status_code == 401
