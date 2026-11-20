"""П9.6 «Токен для ИИ-агентов»: выдать, показать один раз, отозвать; только администратор."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from hrgid.mcp.tokens import K_TOKEN, McpTokens

from .conftest import CO, OTHER, Env


def _issue(admin: TestClient, label: str = "Claude Code Анны") -> str:
    r = admin.post("/settings/tokens", data={"label": label})
    assert r.status_code == 200
    m = re.search(r'value="(hrg_[A-Za-z0-9_-]+)"', r.text)
    assert m, r.text[:300]
    return m.group(1)


def test_admin_issues_token_shown_once_and_stored_hashed(env: Env, admin: TestClient) -> None:
    token = _issue(admin)
    assert McpTokens(env.store).verify(token) == CO
    assert token not in str(env.store.all(K_TOKEN, CO))
    page = admin.get("/settings/tokens").text
    assert token not in page and "Claude Code Анны" in page


def test_admin_revokes_and_token_stops_working(env: Env, admin: TestClient) -> None:
    token = _issue(admin)
    tid = McpTokens(env.store).list(CO)[0]["id"]
    r = admin.post(f"/settings/tokens/{tid}/revoke")
    assert r.status_code == 200 and "отозван" in r.text
    assert McpTokens(env.store).verify(token) is None


def test_hr_cannot_issue_or_revoke(env: Env, hr: TestClient) -> None:
    assert hr.get("/settings/tokens").status_code == 403
    assert hr.post("/settings/tokens", data={"label": "x"}).status_code == 403
    assert McpTokens(env.store).list(CO) == []


def test_admin_cannot_revoke_other_company_token(env: Env, admin: TestClient) -> None:
    other, _ = McpTokens(env.store).issue(OTHER, "чужой", "boss@northwind.test")
    tid = McpTokens(env.store).list(OTHER)[0]["id"]
    admin.post(f"/settings/tokens/{tid}/revoke")
    assert McpTokens(env.store).verify(other) == OTHER


def test_issue_and_revoke_are_journaled(env: Env, admin: TestClient) -> None:
    _issue(admin)
    tid = McpTokens(env.store).list(CO)[0]["id"]
    admin.post(f"/settings/tokens/{tid}/revoke")
    journal = admin.get("/journal").text
    assert "Выдал токен для ИИ-агентов" in journal and "Отозвал токен" in journal
