"""The owner's stand (scripts/run_panel.py) is assembled and opens."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hrgid.panel.app import create_panel_app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def test_owner_stand_is_filled_and_login_works(capsys: pytest.CaptureFixture[str]) -> None:
    import run_panel

    deps = run_panel.build(datetime.now(UTC))
    assert len(deps.repo.employees("studio")) >= 8
    assert len([t for t in deps.repo.tickets("studio") if t.status == "queue"]) == 12
    c = TestClient(create_panel_app(deps), follow_redirects=True)
    c.post("/login", data={"email": run_panel.ADMIN})
    out = capsys.readouterr().out
    code = [w for w in out.split() if w.isdigit() and len(w) == 6][0]
    c.post("/login/code", data={"code": code})
    assert "Студия Жичкина" in c.get("/").text
    assert "Правила работы студии" in c.get("/docs").text
