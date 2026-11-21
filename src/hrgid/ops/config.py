"""Startup settings from the environment, and secrets that are created once and live in the
volume."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from hrgid.panel.deps import PanelDeps  # noqa: F401  # for the types of consumers


def state_dir() -> Path:
    d = Path(os.environ.get("HRGID_STATE_DIR", ".hrgid-state"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def secret(name: str, env: str, directory: Path | None = None) -> str:
    """Secret: from an environment variable, otherwise from a volume file; if there is no file, a
    random one is created."""
    val = os.environ.get(env, "").strip()
    if val:
        return val
    path = (directory or state_dir()) / name
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    val = secrets.token_urlsafe(32)
    path.write_text(val, encoding="utf-8")
    path.chmod(0o600)
    return val


def apply_secrets(directory: Path | None = None) -> None:
    """Puts the hash peppers into the process environment: the panel code and the bot invitation
    codes read them there."""
    os.environ["HRGID_PANEL_PEPPER"] = secret("panel_pepper", "HRGID_PANEL_PEPPER", directory)
    os.environ["HRGID_CODE_PEPPER"] = secret("code_pepper", "HRGID_CODE_PEPPER", directory)


def parse_invites(raw: str) -> dict[str, tuple[str, str]]:
    """ "token=company[:group],..." -> {token: (company, group)}. Empty and broken entries are
    skipped."""
    out: dict[str, tuple[str, str]] = {}
    for part in raw.split(","):
        token, _, target = part.strip().partition("=")
        company, _, group = target.partition(":")
        if token and company:
            out[token] = (company, group)
    return out
