"""П1 «Сегодня»: очередь и сроки, риски в правилах, оценки 👎, расход бюджета."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Form
from fastapi.responses import HTMLResponse, Response

from hrgid.bot.models import Ticket
from hrgid.panel.services import PanelError
from hrgid.panel.web_base import WEEKDAYS, MONTHS, Ctx, Web, human_span


@dataclass(frozen=True)
class RiskLine:
    doc_title: str
    version: int
    open_risks: int
    applicable: bool


def risk_lines(w: Web, c: Ctx) -> list[RiskLine]:
    """Новые риски в правилах: по последней версии каждого документа."""
    out: list[RiskLine] = []
    if w.d.checker is None:
        return out
    for doc in w.d.docs.documents(c.company):
        vs = w.d.docs.versions(c.company, doc["doc_id"])
        if not vs:
            continue
        rep = w.d.checker.report(c.company, doc["doc_id"], vs[-1].number)
        if rep is not None and rep.open_risks:
            out.append(
                RiskLine(
                    doc["title"], vs[-1].number, len(rep.open_risks), vs[-1].applicable(c.today)
                )
            )
    return out


def today_page(
    w: Web, c: Ctx, *, win: str = "", key: str = "", errors: dict[str, str] | None = None
) -> HTMLResponse:
    d = w.d
    views = d.desk.list_tickets(c.company, "all", c.now, c.user.email)
    tickets = {t.key: t for t in d.repo.tickets(c.company)}
    mine = [v for v in views if v.assignee == c.user.email]
    overdue = [v for v in views if v.overdue]
    downs = d.ratings.open_items(c.company, c.now)
    oldest = ""
    if views:
        first = min(tickets[v.key].created_at for v in views)
        oldest = f"старейший ждёт {human_span(c.now - first)}"
    budget = d.ops.budget(c.company, c.now)
    risks = risk_lines(w, c)
    is_new = not d.docs.documents(c.company) and not d.repo.employees(c.company)
    waits = {v.key: human_span(c.now - tickets[v.key].created_at) for v in views}
    local = c.local(c.now)
    date_line = f"{WEEKDAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]}"
    hours_line = f"HR на связи с {c.settings.hours_start:%H:%M} до {c.settings.hours_end:%H:%M}"
    rating_ticket: Ticket | None = None
    if win == "rating":
        rating_ticket = d.ratings.get(c.company, key)
    return w.page(
        c, "today.html", "today", win=win, views=views, waits=waits, mine=mine, overdue=overdue,
        downs=downs, oldest=oldest, budget=budget, risks=risks, is_new=is_new,
        date_line=date_line, hours_line=hours_line, sla=c.settings.sla_hours,
        rating_ticket=rating_ticket, errors=errors or {}, empty=not views and not risks and not downs,
        section_title="Сегодня",
    )  # fmt: skip


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    @app.get("/", response_class=HTMLResponse)
    def today(c: Annotated[Ctx, Depends(w.ctx)]) -> HTMLResponse:
        return today_page(w, c)

    @app.get("/loading", response_class=HTMLResponse)
    def loading(c: Annotated[Ctx, Depends(w.ctx)]) -> HTMLResponse:
        return w.page(c, "state.html", "today", kind="loading", title="Загрузка…")

    @app.get("/rating/{key}", response_class=HTMLResponse)
    def rating(key: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        try:
            return today_page(w, c, win="rating", key=key)
        except PanelError:
            return w.error_page(c, "today", "Оценка не найдена", 404)

    @app.post("/rating/{key}")
    def rating_do(
        key: str, c: Annotated[Ctx, Depends(w.ctx)], decision: Annotated[str, Form()] = "ok"
    ) -> Response:
        try:
            t = d.ratings.get(c.company, key)
        except PanelError:
            return w.error_page(c, "today", "Оценка не найдена", 404)
        d.ratings.resolve(c.company, key, c.now, c.user.email)
        d.journal.add(c.company, c.now, c.user.email, "rating.resolve", t.label)
        if decision == "fix":
            return w.see_other(f"/kb/new?from={key}")
        w.flash(c, "Оценка разобрана.")
        return w.see_other("/")
