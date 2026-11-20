"""Приложение панели HR: сборка маршрутов, защита, обработчики ошибок и пустых прав."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from hrgid.panel import (
    routes_auth,
    routes_docs,
    routes_insights,
    routes_kb,
    routes_queue,
    routes_settings,
    routes_staff,
    routes_today,
)
from hrgid.panel.deps import PanelDeps
from hrgid.panel.maskinglog import install as install_masking
from hrgid.panel.web_base import COOKIE, HERE, Ctx, Redirect, Web

log = logging.getLogger("hrgid.panel")


def create_panel_app(deps: PanelDeps) -> FastAPI:
    install_masking()
    app = FastAPI(title="HRgid — панель HR", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bot_username = deps.bot_username
    w = Web(deps)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def secure(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Подделка запроса с другого сайта: у изменяющих запросов Origin/Referer должен быть наш.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            src = request.headers.get("origin") or request.headers.get("referer")
            host = request.headers.get("host", "")
            if src and urlparse(src).netloc != host:
                return JSONResponse({"detail": "Запрос с чужого сайта отклонён"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.exception_handler(Redirect)
    async def redirect_handler(request: Request, exc: Redirect) -> Response:
        resp = w.see_other(exc.url)
        if exc.clear_cookie:
            resp.delete_cookie(COOKIE, path="/")
        return resp

    def _ctx_or_none(request: Request) -> Ctx | None:
        try:
            return w.ctx(request)
        except Redirect:
            return None

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> Response:
        c = _ctx_or_none(request)
        if c is None or request.url.path.startswith("/static"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if exc.status_code == 403:
            return w.page(
                c, "state.html", "", 403, kind="noaccess", title="Нет прав",
                text="Это действие доступно администратору компании.", ask_link="/costs/ask",
            )  # fmt: skip
        if exc.status_code == 404:
            return w.page(c, "state.html", "", 404, kind="noaccess", title="Не найдено",
                          text="Такой страницы или записи нет.")  # fmt: skip
        return w.page(c, "state.html", "", exc.status_code, kind="error", title="Ошибка")

    @app.exception_handler(Exception)
    async def crash_handler(request: Request, exc: Exception) -> Response:
        # Текст исключения может содержать данные сотрудников: в журнал пишем только тип.
        log.error("сбой в панели: %s на %s", type(exc).__name__, request.url.path)
        c = _ctx_or_none(request)
        if c is None:
            return JSONResponse({"detail": "Ошибка сервиса"}, status_code=500)
        return w.error_page(c, "", "Раздел")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    for mod in (
        routes_auth,
        routes_today,
        routes_queue,
        routes_kb,
        routes_docs,
        routes_staff,
        routes_insights,
        routes_settings,
    ):
        mod.register(app, w)
    return app


__all__ = ["HTMLResponse", "create_panel_app"]
