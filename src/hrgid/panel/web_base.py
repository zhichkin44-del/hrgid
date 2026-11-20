"""Общее для всех маршрутов панели: сессия, права, шаблоны, плашки-уведомления, ошибки."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from hrgid.bot import hours
from hrgid.bot.models import CompanySettings
from hrgid.panel.deps import PanelDeps
from hrgid.panel.perms import ROLE_NAMES, can, can_section
from hrgid.panel.security import PanelUser

HERE = Path(__file__).parent
COOKIE = "hrgid_sid"
K_FLASH = "pflash"

NAV: list[tuple[str, str, str]] = [
    ("today", "/", "Сегодня"),
    ("queue", "/queue", "Очередь"),
    ("kb", "/kb", "База ответов"),
    ("docs", "/docs", "Документы"),
    ("check", "/check", "Проверка на ТК"),
    ("staff", "/staff", "Сотрудники"),
    ("ann", "/announcements", "Объявления"),
    ("analytics", "/analytics", "Аналитика"),
    ("costs", "/costs", "Расходы и качество"),
    ("settings", "/settings", "Настройки"),
    ("journal", "/journal", "Журнал"),
]
MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip
MONTHS_PREP = [
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
]  # fmt: skip
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class Redirect(Exception):
    def __init__(self, url: str, clear_cookie: bool = False) -> None:
        self.url = url
        self.clear_cookie = clear_cookie


@dataclass
class Ctx:
    """Контекст запроса: кто вошёл, в какой компании, который час."""

    request: Request
    user: PanelUser
    sid: str
    now: datetime
    settings: CompanySettings

    @property
    def company(self) -> str:
        return self.user.company_id

    @property
    def role(self) -> str:
        return self.user.role

    def local(self, at: datetime) -> datetime:
        return hours.to_local(at, self.settings)

    @property
    def today(self) -> date:
        return self.local(self.now).date()


def fmt_dt(at: datetime | None, s: CompanySettings) -> str:
    if at is None:
        return ""
    return hours.to_local(at, s).strftime("%d.%m.%Y %H:%M")


def fmt_date(d: date | datetime | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def human_span(delta: timedelta) -> str:
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 60:
        return f"{mins} мин"
    h, m = divmod(mins, 60)
    if h < 24:
        return f"{h} ч {m:02d} мин" if m else f"{h} ч"
    d, h = divmod(h, 24)
    return f"{d} д {h} ч"


def rub(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " ₽"


class Web:
    def __init__(self, deps: PanelDeps) -> None:
        self.d = deps
        self.templates = Jinja2Templates(directory=str(HERE / "templates"))
        env = self.templates.env
        env.globals.update(
            can=can, can_section=can_section, role_names=ROLE_NAMES, rub=rub, fmt_date=fmt_date,
            human_span=human_span,
        )  # fmt: skip
        env.filters["todt"] = datetime.fromisoformat

    # --- сессия -----------------------------------------------------------------------
    def now(self) -> datetime:
        return self.d.clock()

    def ctx(self, request: Request) -> Ctx:
        """Зависимость маршрута: пользователь из cookie или переход на вход."""
        now = self.now()
        token = request.cookies.get(COOKIE, "")
        res = self.d.auth.session(token, now) if token else None
        if res == "expired":
            raise Redirect("/login/expired", clear_cookie=True)
        if res is None:
            raise Redirect("/login")
        session, user = res
        self.d.undo.flush(user.company_id, now)
        return Ctx(request, user, session.sid, now, self.d.repo.settings(user.company_id))

    def require(self, c: Ctx, action: str) -> None:
        """Права по реестру: 403, если действие только для администратора."""
        if not can(c.role, action):
            raise HTTPException(403, "Доступно администратору")

    # --- плашки --------------------------------------------------------------------------
    def flash(self, c: Ctx, text: str, *, kind: str = "ok", undo: str = "") -> None:
        key = c.sid
        d = self.d.store.get(K_FLASH, c.company, key) or {"items": []}
        d["items"].append({"text": text, "kind": kind, "undo": undo})
        self.d.store.put(K_FLASH, c.company, key, d)

    def pop_flash(self, c: Ctx) -> list[dict[str, str]]:
        d = self.d.store.get(K_FLASH, c.company, c.sid)
        if not d or not d["items"]:
            return []
        self.d.store.put(K_FLASH, c.company, c.sid, {"items": []})
        items: list[dict[str, str]] = d["items"]
        return items

    # --- страницы ---------------------------------------------------------------------------
    def nav_counts(self, c: Ctx) -> dict[str, tuple[int, bool]]:
        """Числа у разделов меню; при сбое хранилища меню остаётся без чисел."""
        try:
            queue = self.d.desk.list_tickets(c.company, "all", c.now, c.user.email)
            risks = self.risk_count(c) if self.d.checker is not None else 0
        except Exception:
            return {}
        return {"queue": (len(queue), False), "check": (risks, True)}

    def risk_count(self, c: Ctx) -> int:
        n = 0
        for doc in self.d.docs.documents(c.company):
            vs = self.d.docs.versions(c.company, doc["doc_id"])
            if not vs or self.d.checker is None:
                continue
            rep = self.d.checker.report(c.company, doc["doc_id"], vs[-1].number)
            if rep is not None:
                n += len(rep.open_risks)
        return n

    def page(
        self, c: Ctx, template: str, section: str, status: int = 200, **kw: Any
    ) -> HTMLResponse:
        counts = self.nav_counts(c)
        nav = [(key, url, label, counts.get(key, (0, False))) for key, url, label in NAV]
        ctx: dict[str, Any] = {
            "c": c,
            "user": c.user,
            "section": section,
            "nav": nav,
            "company_name": c.settings.company_name,
            "flash": self.pop_flash(c),
            "now_local": c.local(c.now),
            "tz": c.settings.tz_label,
            "settings": c.settings,
            "errors": {},
            "form": {},
            "win": "",
            "retry": c.request.url.path,
        }
        ctx.update(kw)
        resp = self.templates.TemplateResponse(c.request, template, ctx, status_code=status)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def public(self, request: Request, template: str, status: int = 200, **kw: Any) -> HTMLResponse:
        resp = self.templates.TemplateResponse(request, template, kw, status_code=status)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def error_page(self, c: Ctx, section: str, title: str, status: int = 500) -> HTMLResponse:
        return self.page(c, "state.html", section, status, kind="error", title=title)

    def see_other(self, url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def set_cookie(self, resp: Response, token: str) -> None:
        resp.set_cookie(
            COOKIE, token, httponly=True, samesite="lax", secure=self.d.secure_cookie,
            max_age=60 * 60 * 24, path="/",
        )  # fmt: skip

    def local_str(self, c: Ctx, at: datetime | None) -> str:
        return fmt_dt(at, c.settings)
