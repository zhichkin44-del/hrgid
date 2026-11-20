"""П0: вход по почте и одноразовому коду, выход, истёкшая сессия."""

from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response

from hrgid.panel.mailer import mask_email
from hrgid.panel.security import MAX_FAILS
from hrgid.panel.web_base import COOKIE, Ctx, Web

EMAIL_COOKIE = "hrgid_le"  # почта на время входа: в адресной строке не храним


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    def _ip(request: Request) -> str:
        return request.client.host if request.client else ""

    def _code_page(
        request: Request,
        email: str,
        error: str = "",
        detail: str = "",
        wait: int = 0,
        status: int = 200,
    ) -> HTMLResponse:
        return w.public(
            request, "login.html", status, step="code", masked=mask_email(email), error=error,
            detail=detail, wait=wait,
        )  # fmt: skip

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> HTMLResponse:
        return w.public(request, "login.html", step="email")

    @app.get("/login/expired", response_class=HTMLResponse)
    def login_expired(request: Request) -> HTMLResponse:
        resp = w.public(request, "login.html", step="expired")
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/login", response_class=HTMLResponse)
    def login_post(request: Request, email: Annotated[str, Form()] = "") -> Response:
        res = d.auth.request_code(email, w.now(), _ip(request))
        if res.status == "bad_email":
            return w.public(
                request, "login.html", 200, step="email", email=email,
                error="Проверьте почту: нужен адрес вида name@company.ru.",
            )  # fmt: skip
        if res.status == "no_access":
            return w.public(request, "login.html", 200, step="noaccess")
        if res.status == "too_soon":
            resp = _code_page(
                request, email, "Код уже отправлен.", "Новый — через минуту.", res.wait_sec
            )
        else:
            resp = _code_page(request, email)
        resp.set_cookie(EMAIL_COOKIE, email.strip().lower(), httponly=True, samesite="strict",
                        max_age=900, path="/")  # fmt: skip
        return resp

    @app.post("/login/resend", response_class=HTMLResponse)
    def login_resend(request: Request) -> Response:
        email = request.cookies.get(EMAIL_COOKIE, "")
        if not email:
            return w.public(request, "login.html", step="email")
        res = d.auth.request_code(email, w.now(), _ip(request))
        if res.status == "too_soon":
            return _code_page(
                request, email, "Код уже отправлен.", "Новый — через минуту.", res.wait_sec
            )
        return _code_page(request, email)

    @app.post("/login/code", response_class=HTMLResponse)
    def login_code(request: Request, code: Annotated[str, Form()] = "") -> Response:
        email = request.cookies.get(EMAIL_COOKIE, "")
        if not email:
            return w.public(request, "login.html", step="email")
        res = d.auth.verify(email, code, w.now(), _ip(request))
        if res.status == "ok":
            resp = w.see_other("/")
            w.set_cookie(resp, res.token)
            resp.delete_cookie(EMAIL_COOKIE, path="/")
            return resp
        if res.status == "bad":
            return _code_page(
                request, email, "Код неверный.",
                f"Осталось {res.left} из {MAX_FAILS} попыток. После пяти ошибок подряд вход "
                "закроется на 15 минут, запись попадёт в журнал входов.", status=200,
            )  # fmt: skip
        if res.status == "blocked":
            return _code_page(
                request, email, "Вход закрыт на 15 минут.",
                "Слишком много неверных кодов. Попробуйте позже или попросите администратора.",
            )  # fmt: skip
        if res.status == "expired":
            return _code_page(request, email, "Код просрочен.", "Запросите новый код.")
        return w.public(request, "login.html", step="email")

    @app.get("/logout", response_class=HTMLResponse)
    def logout_ask(request: Request, c: Annotated[Ctx, Depends(w.ctx)]) -> HTMLResponse:
        """П0.5 «Выйти из панели?»: окно поверх «Сегодня»."""
        from hrgid.panel.routes_today import today_page

        return today_page(w, c, win="logout")

    @app.post("/logout")
    def logout_do(request: Request) -> Response:
        d.auth.logout(request.cookies.get(COOKIE, ""), w.now())
        resp = w.see_other("/login")
        resp.delete_cookie(COOKIE, path="/")
        return resp
