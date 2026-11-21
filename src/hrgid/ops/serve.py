"""The api service: HR panel and web chat in one FastAPI process (production build, PostgreSQL)."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from hrgid.flow.build import PROVIDERS, make_client, provider_from_env
from hrgid.llm.base import LLMClient
from hrgid.llm.resilient import ResilientLLM
from hrgid.ops.config import apply_secrets, parse_invites, secret, state_dir

log = logging.getLogger("hrgid.ops.api")


def make_llm(provider: str) -> LLMClient:
    """Provider from HRGID_LLM_PROVIDER; "fake" means running without keys (all answers are
    refusals)."""
    if provider not in (*PROVIDERS, "fake"):
        raise SystemExit(f"неизвестный HRGID_LLM_PROVIDER: {provider}; нужен {PROVIDERS} или fake")
    if provider == "fake":
        log.warning("модель не подключена (HRGID_LLM_PROVIDER=fake): все ответы будут отказами")
    return ResilientLLM(make_client(provider))


def create_api() -> FastAPI:
    from hrgid.bot.kb import KbSearcher
    from hrgid.bot.store import PgDocStore
    from hrgid.db import database_url, init_schema
    from hrgid.flow.build import make_searcher
    from hrgid.flow.graph import FlowConfig, Pipeline
    from hrgid.ops.bootstrap import wait_for_db
    from hrgid.panel.app import create_panel_app
    from hrgid.panel.deps import build_deps
    from hrgid.panel.mailer import FileMailer, LogMailer, Mailer
    from hrgid.panel.wiring import make_panel_traces
    from hrgid.web import Invite, create_app

    apply_secrets()
    conn = wait_for_db(database_url())
    init_schema(conn)
    store = PgDocStore(conn)
    store.init_schema()
    state = state_dir()
    # Without SMTP the sign-in code is kept as a file in the volume (docker compose exec api cat
    # ...); LOG logs only the fact.
    mailer: Mailer = (
        LogMailer() if os.environ.get("HRGID_MAIL") == "log" else FileMailer(state / "outbox")
    )
    deps = build_deps(
        store,
        mailer=mailer,
        pepper=os.environ["HRGID_PANEL_PEPPER"],
        bot_username=os.environ.get("HRGID_BOT_USERNAME", "hrgid_bot"),
        secure_cookie=os.environ.get("HRGID_SECURE_COOKIE") == "1",
    )
    llm = make_llm(provider_from_env("fake"))
    searcher = KbSearcher(
        make_searcher(conn, os.environ.get("HRGID_RERANKER") or "mmarco-minilm"), deps.kb
    )
    threshold = os.environ.get("HRGID_NO_ANSWER_THRESHOLD")
    pipeline = Pipeline(
        llm, llm, searcher, traces=make_panel_traces(deps, conn),
        config=FlowConfig(no_answer_threshold=float(threshold) if threshold else None),
    )  # fmt: skip

    extra = parse_invites(os.environ.get("HRGID_WEB_INVITES", ""))
    invites = {token: Invite(company, group) for token, (company, group) in extra.items()}
    company_id = os.environ.get("HRGID_COMPANY", "")
    if company_id:
        invites[secret("web_invite", "HRGID_WEB_INVITE", state)] = Invite(company_id)

    app = create_panel_app(deps)
    app.mount("/", create_app(pipeline, invites))  # web chat: /c/{token}, /api/chat
    log.info("api готов: панель /, веб-чат /c/<токен>, приглашений: %d", len(invites))
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_api(), host="0.0.0.0", port=port, log_level="info")  # noqa: S104
