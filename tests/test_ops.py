"""Эксплуатация: секреты тома, приглашения, первая компания, работник, роли запуска."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from hrgid.bot.repo import Repo
from hrgid.bot.store import MemoryDocStore
from hrgid.llm.fake import NO_MODEL_ANSWER
from hrgid.ops import launch
from hrgid.ops.bootstrap import ensure_company, needs_index
from hrgid.ops.config import apply_secrets, parse_invites, secret
from hrgid.ops.serve import make_llm
from hrgid.ops.worker import HEARTBEAT_KEY, beat, run_forever


def test_secret_is_generated_once_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("X_PEPPER", raising=False)
    first = secret("pepper", "X_PEPPER", tmp_path)
    assert len(first) >= 32
    assert secret("pepper", "X_PEPPER", tmp_path) == first
    monkeypatch.setenv("X_PEPPER", "from-env")
    assert secret("pepper", "X_PEPPER", tmp_path) == "from-env"


def test_apply_secrets_sets_both_peppers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HRGID_PANEL_PEPPER", "HRGID_CODE_PEPPER"):
        monkeypatch.delenv(name, raising=False)
    apply_secrets(tmp_path)
    assert os.environ["HRGID_PANEL_PEPPER"] != os.environ["HRGID_CODE_PEPPER"]
    monkeypatch.delenv("HRGID_PANEL_PEPPER")
    monkeypatch.delenv("HRGID_CODE_PEPPER")


def test_parse_invites() -> None:
    got = parse_invites("t1=studio, t2=acme:Дизайн,broken,=x,t3=")
    assert got == {"t1": ("studio", ""), "t2": ("acme", "Дизайн")}


def test_needs_index() -> None:
    assert needs_index(0, 3000) and needs_index(10, 3000) and not needs_index(3000, 3000)


def test_ensure_company_is_idempotent() -> None:
    repo = Repo(MemoryDocStore())
    added: list[tuple[Any, ...]] = []

    def add_user(*a: Any) -> None:
        added.append(a)

    assert ensure_company(repo, add_user, "studio", "Студия", "a@x.ru", "Админ") is True
    assert ensure_company(repo, add_user, "studio", "Студия", "a@x.ru", "Админ") is False
    assert repo.settings("studio").company_name == "Студия"
    assert added[0] == ("studio", "a@x.ru", "Админ", "admin")
    assert ensure_company(repo, add_user, "", "", "", "") is False


def test_fake_provider_always_refuses() -> None:
    from hrgid.llm.base import LLMRequest, Message

    llm = make_llm("fake")
    r = llm.complete(LLMRequest("s", (Message("user", "q"),), "main", purpose="answer"))
    assert r.text == NO_MODEL_ANSWER
    with pytest.raises(SystemExit):
        make_llm("gpt")


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, tuple[str, int]] = {}

    def set(self, key: str, value: str, ex: int) -> None:
        self.data[key] = (value, ex)


def test_heartbeat_has_ttl() -> None:
    r = FakeRedis()
    beat(r, 1000.0, ttl=90)
    assert r.data[HEARTBEAT_KEY] == ("1000", 90)


def test_worker_survives_failing_step() -> None:
    calls: list[int] = []

    def step() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("сбой")

    run_forever(step, every=0, sleep=lambda _s: None, rounds=3)
    assert len(calls) == 3


def test_launch_rejects_unknown_role_and_idles_without_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HRGID_STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        launch.run("nope")
    reasons: list[str] = []
    monkeypatch.setattr(launch, "idle", reasons.append)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("hrgid.bot.app.main", lambda: None)
    launch.run("bot")
    assert "TELEGRAM_BOT_TOKEN" in reasons[0]
    started: list[str] = []
    monkeypatch.setattr("runpy.run_module", lambda name, run_name: started.append(name))
    launch.run("mcp")
    assert started == ["hrgid.mcp"]
    assert len(reasons) == 1
