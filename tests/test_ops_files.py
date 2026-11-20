"""Статическая проверка файлов эксплуатации (Docker нет на машине разработки; запуск — в CI).

Compose, Dockerfile, .env.example, ci.yml и README проверяются как данные: роли, healthcheck,
порядок запуска, непривилегированный пользователь, согласованность переменных.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from hrgid.ops.launch import ROLES

ROOT = Path(__file__).resolve().parents[1]


def load(rel: str) -> dict[Any, Any]:
    data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return load("docker-compose.yml")


def test_compose_has_all_services(compose: dict[str, Any]) -> None:
    assert set(compose["services"]) == {"postgres", "redis", "init", "api", "worker", "bot", "mcp"}


def test_postgres_is_pgvector_with_volume_and_healthcheck(compose: dict[str, Any]) -> None:
    pg = compose["services"]["postgres"]
    assert pg["image"] == "pgvector/pgvector:pg16"
    assert any("pgdata" in v for v in pg["volumes"]) and "pgdata" in compose["volumes"]
    assert "pg_isready" in " ".join(pg["healthcheck"]["test"])
    assert "healthcheck" in compose["services"]["redis"]


def test_start_order_init_waits_for_db_and_apps_wait_for_init(compose: dict[str, Any]) -> None:
    svc = compose["services"]
    assert svc["init"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    for name in ("api", "worker", "bot", "mcp"):
        dep = svc[name]["depends_on"]["init"]["condition"]
        assert dep == "service_completed_successfully", name
    assert svc["worker"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert svc["init"]["restart"] == "no"


def test_roles_in_commands_exist_and_api_is_published(compose: dict[str, Any]) -> None:
    for name in ("init", "api", "worker", "bot", "mcp"):
        cmd = compose["services"][name]["command"]
        assert cmd[:3] == ["python", "-m", "hrgid.ops.launch"] and cmd[3] in ROLES
        assert cmd[3] == name
    api = compose["services"]["api"]
    assert "healthcheck" in api and any("8000" in p for p in api["ports"])
    # база и redis наружу не опубликованы
    assert "ports" not in compose["services"]["postgres"]
    assert "ports" not in compose["services"]["redis"]


def test_dockerfile_is_multistage_nonroot_with_separate_model_layer() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG PYTHON=python:3.12-slim" in text
    stages = re.findall(r"^FROM .* AS (\w+)", text, re.M)
    assert stages == ["deps", "models", "runtime"]
    assert re.search(r"^USER hrgid$", text, re.M)
    assert text.index("AS models") < text.index("COPY --chown=hrgid:hrgid src")
    assert "COPY --from=models" in text and "HF_HUB_OFFLINE=1" in text
    assert "multilingual-e5-small" in text and "mmarco" in text


def test_env_example_has_no_values_and_covers_compose_variables(compose: dict[str, Any]) -> None:
    keys: set[str] = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        assert sep == "=" and value == "", f"в .env.example нет места значениям: {line}"
        keys.add(key)
    used = set(re.findall(r"\$\{(\w+)", (ROOT / "docker-compose.yml").read_text(encoding="utf-8")))
    assert used <= keys, used - keys
    assert {"TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "HRGID_PROVIDER"} <= keys


def test_secrets_are_not_baked_into_files() -> None:
    for rel in ("docker-compose.yml", "Dockerfile", ".env.example"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"sk-ant|\d{9,}:[\w-]{30,}", text), rel


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return load(".github/workflows/ci.yml")


def steps_text(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) + str(s.get("uses", "")) for s in job["steps"])


def test_ci_jobs_and_gates(ci: dict[str, Any]) -> None:
    jobs = ci["jobs"]
    assert {"check", "e2e", "quality-gate", "compose-smoke"} <= set(jobs)
    check = steps_text(jobs["check"])
    assert "ruff check" in check and "mypy" in check and "pytest" in check
    assert "pgvector/pgvector:pg16" in jobs["check"]["services"]["postgres"]["image"]
    assert "playwright install" in steps_text(jobs["e2e"]) and "-m browser" in steps_text(
        jobs["e2e"]
    )
    smoke = steps_text(jobs["compose-smoke"])
    assert "docker compose up -d" in smoke and "docker compose down" in smoke
    assert "/healthz" in smoke and "/c/" in smoke


def test_quality_gate_skips_with_warning_when_pending(ci: dict[str, Any]) -> None:
    steps = ci["jobs"]["quality-gate"]["steps"]
    text = steps_text(ci["jobs"]["quality-gate"])
    assert "tests/recordings/PENDING" in text and "::warning" in text
    assert "tests/test_quality_gate.py" in text  # ворота доказанно краснеют
    gated = [s for s in steps if "python scripts/quality_gate.py" in str(s.get("run", ""))]
    assert gated and gated[0]["if"] == "steps.rec.outputs.pending == 'false'"


def test_eval_retrieval_workflow_is_manual_only() -> None:
    wf = load(".github/workflows/eval-retrieval.yml")
    assert list(wf[True]) == ["workflow_dispatch"]  # YAML: ключ on == True


def test_required_docs_exist() -> None:
    for rel in ("README.md", "docs/threat-model.md", "docs/incident-response.md", "docs/adr"):
        assert (ROOT / rel).exists(), rel
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in (
        "docker compose up",
        "```mermaid",
        "появится после живого прогона",
        "152-ФЗ",
        "docs/threat-model.md",
        "docs/incident-response.md",
        "docs/adr",
        "## English",
        "по решению владельца",
    ):
        assert needle in readme, needle
    assert "72" in (ROOT / "docs/incident-response.md").read_text(encoding="utf-8")


SECRET = re.compile(
    r"sk-ant-[\w-]{20,}|\b\d{8,10}:[A-Za-z0-9_-]{35}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def _git(*args: str) -> str:
    import shutil
    import subprocess

    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("нет git или истории")
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False
    ).stdout


def test_no_secrets_in_history() -> None:
    """Ключи моделей, токены Telegram и закрытые ключи не попадали ни в один коммит."""
    commits = _git("rev-list", "--all").split()
    if not commits:
        pytest.skip("пустая история")
    hits = _git("grep", "-I", "-l", "-E", SECRET.pattern, *commits[:2000])
    assert hits.strip() == "", hits
