"""П2 «Очередь»: обращения, черновик, шаблон из базы, назначение, ответ, сохранение в базу."""

import re
from dataclasses import dataclass, field
from typing import Annotated, Literal, cast

from fastapi import Depends, FastAPI, Form, Query
from fastapi.responses import HTMLResponse, Response

from hrgid.bot.desk import DeskError, KbParams
from hrgid.bot.kb import KbError
from hrgid.bot.models import Employee, Ticket
from hrgid.panel.security import PanelUser
from hrgid.panel.services import K_NOTE, PanelError
from hrgid.panel.web_base import Ctx, Web, human_span

Tab = Literal["all", "common", "mine", "conf", "overdue"]
TABS: tuple[tuple[str, str], ...] = (
    ("all", "Все"),
    ("common", "Общая"),
    ("mine", "Мои"),
    ("conf", "Конфиденциальные"),
    ("overdue", "Просроченные"),
)


@dataclass
class Suggestion:
    summary: str = ""
    found: list[tuple[str, str]] = field(default_factory=list)  # (источник, цитата)
    draft: str = ""


def suggest(w: Web, c: Ctx, t: Ticket) -> Suggestion:
    """Пересказ, найденные статьи и черновик. Источник — подключаемый помощник (extras['suggest']),
    без него — похожие ответы из базы HR."""
    fn = w.d.extras.get("suggest")
    if t.confidential:
        return Suggestion()
    if fn is not None:
        res: Suggestion = fn(c.company, t.text)
        return res
    sug = Suggestion()
    ranked = w.d.kb.ranked(c.company, t.text)[:3]
    for _, e in ranked:
        sug.found.append((e.basis, e.answer[:160]))
    if ranked:
        sug.draft = ranked[0][1].answer
    return sug


def colleagues(w: Web, c: Ctx) -> list[PanelUser]:
    return [u for u in w.d.auth.users(c.company) if u.active and u.email != c.user.email]


def queue_page(
    w: Web,
    c: Ctx,
    *,
    tab: str = "all",
    key: str = "",
    win: str = "",
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    tab = tab if tab in dict(TABS) else "all"
    views = d.desk.list_tickets(c.company, cast(Tab, tab), c.now, c.user.email)
    counts = {
        k: len(d.desk.list_tickets(c.company, cast(Tab, k), c.now, c.user.email)) for k, _ in TABS
    }
    sel: Ticket | None = None
    if key:
        sel = d.repo.ticket(c.company, key)
        if sel is not None and sel.status != "queue" and win not in ("answered",):
            sel = None if sel.status != "answered" else sel
    if sel is None and views and not key:
        sel = d.repo.ticket(c.company, views[0].key)
    emp: Employee | None = None
    hist: list[Ticket] = []
    sug = Suggestion()
    by_key = {t.key: t for t in d.repo.tickets(c.company)}
    waits = {v.key: human_span(c.now - by_key[v.key].created_at) for v in views}
    if sel is not None:
        emp = d.repo.employee(c.company, sel.employee_id)
        if not sel.confidential and emp is not None:
            hist = [
                t for t in d.desk.employee_tickets(emp) if t.key != sel.key and not t.confidential
            ][:5]
        sug = suggest(w, c, sel)
    form = dict(form or {})
    if sel is not None and "text" not in form:
        form["text"] = sug.draft
    tpl_results = []
    if win == "tpl":
        tpl_results = d.kbadmin.entries(c.company, query=form.get("q", ""))[:8]
    due_note = ""
    if sel is not None and sel.status == "queue":
        if c.now > sel.due_at:
            due_note = f"Просрочено на {human_span(c.now - sel.due_at)}"
        else:
            due_note = f"Осталось {human_span(sel.due_at - c.now)}"
    return w.page(
        c, "queue.html", "queue", status, tab=tab, tabs=TABS, counts=counts, views=views,
        waits=waits, sel=sel, emp=emp, hist=hist, sug=sug, win=win, form=form,
        errors=errors or {}, tpl_results=tpl_results, due_note=due_note,
        overdue=bool(sel and sel.status == "queue" and c.now > sel.due_at),
        mates=colleagues(w, c), groups=d.staff.groups(c.company),
        hidden=bool(sel and sel.confidential and not sel.revealed), section_title="Очередь",
    )  # fmt: skip


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    @app.get("/queue", response_class=HTMLResponse)
    def queue(
        c: Annotated[Ctx, Depends(w.ctx)],
        tab: str = "all",
        k: str = "",
        w_: Annotated[str, Query(alias="w")] = "",
        q: str = "",
    ) -> Response:
        win = ""
        if w_ == "history":
            win = "history"
        return queue_page(w, c, tab=tab, key=k, win=win, form={"q": q} if q else None)

    @app.get("/queue/{key}/history", response_class=HTMLResponse)
    def history(key: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return queue_page(w, c, key=key, win="history")

    @app.post("/queue/{key}/w/{win}", response_class=HTMLResponse)
    def card_window(
        key: str,
        win: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        text: Annotated[str, Form()] = "",
        q: Annotated[str, Form()] = "",
    ) -> Response:
        """Окна над карточкой (отправить / шаблон / назначить / в базу): черновик не теряется."""
        if win not in ("send", "tpl", "assign", "savekb"):
            return queue_page(w, c, key=key)
        t = d.repo.ticket(c.company, key)
        if t is None:
            return w.error_page(c, "queue", "Обращение не найдено", 404)
        form = {"text": text, "q": q, "question": t.text, "answer": text}
        return queue_page(w, c, key=key, win=win, form=form)

    @app.post("/queue/{key}/tpl", response_class=HTMLResponse)
    def apply_template(
        key: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        entry: Annotated[int, Form()],
    ) -> Response:
        try:
            e = d.kbadmin.get(c.company, entry)
        except PanelError:
            return w.error_page(c, "queue", "Ответ не найден", 404)
        w.flash(c, "Шаблон подставлен в черновик. Проверьте текст перед отправкой.")
        return queue_page(w, c, key=key, form={"text": e.answer})

    @app.post("/queue/{key}/assign", response_class=HTMLResponse)
    def assign(
        key: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        to: Annotated[str, Form()] = "",
        comment: Annotated[str, Form()] = "",
        text: Annotated[str, Form()] = "",
    ) -> Response:
        t = d.repo.ticket(c.company, key)
        if t is None:
            return w.error_page(c, "queue", "Обращение не найдено", 404)
        mate = next((u for u in colleagues(w, c) if u.email == to), None)
        err: dict[str, str] = {}
        if mate is None:
            err["to"] = "Выберите коллегу."
        elif mate.away:
            err["to"] = "Коллега в отпуске: назначить ему нельзя."
        if len(comment) > 500:
            err["comment"] = "Комментарий — не длиннее 500 знаков."
        if err:
            return queue_page(
                w,
                c,
                key=key,
                win="assign",
                form={"to": to, "comment": comment, "text": text},
                errors=err,
            )
        assert mate is not None
        try:
            d.desk.assign(c.company, key, mate.email)
        except DeskError as e:
            return queue_page(w, c, key=key, win="assign", errors={"to": str(e)})
        if comment.strip():
            n = d.store.next_seq(c.company, "pnote")
            d.store.put(K_NOTE, c.company, f"{n:08d}",
                        {"ticket": key, "by": c.user.email, "text": comment.strip()})  # fmt: skip
        d.journal.add(c.company, c.now, c.user.email, "ticket.assign", f"{t.label} → {mate.name}")
        w.flash(c, f"Вопрос назначен: {mate.name}.")
        return w.see_other("/queue")

    def _kb_params(t: Ticket, form: dict[str, str]) -> KbParams:
        return KbParams(
            question=form.get("question", t.text), answer=form.get("answer", ""),
            basis=form.get("basis", ""), audience=form.get("audience") or "all",
        )  # fmt: skip

    @app.post("/queue/{key}/answer", response_class=HTMLResponse)
    def answer(
        key: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        text: Annotated[str, Form()] = "",
        save_kb: Annotated[str, Form()] = "",
        question: Annotated[str, Form()] = "",
        basis: Annotated[str, Form()] = "",
        audience: Annotated[str, Form()] = "all",
    ) -> Response:
        t = d.repo.ticket(c.company, key)
        if t is None:
            return w.error_page(c, "queue", "Обращение не найдено", 404)
        form = {"text": text, "question": question or t.text, "answer": text, "basis": basis,
                "audience": audience, "save_kb": save_kb}  # fmt: skip
        if not text.strip():
            return queue_page(w, c, key=key, form=form, errors={"text": "Напишите ответ."})
        params = _kb_params(t, form) if save_kb and not t.confidential else None
        try:
            t2, entry = d.desk.answer(c.company, key, c.user.email, text, c.now, save_to_kb=params)
        except (KbError, PanelError) as e:
            fld = getattr(e, "field", "basis")
            return queue_page(w, c, key=key, win="send", form=form, errors={fld: str(e)})
        except DeskError as e:
            return queue_page(w, c, key=key, form=form, errors={"text": str(e)})
        d.journal.add(c.company, c.now, c.user.email, "ticket.answer", t2.label)
        if entry is not None:
            d.journal.add(c.company, c.now, c.user.email, "kb.add", f"#{entry.id}")
        if t2.delivery_failed:
            w.flash(
                c,
                "Ответ сохранён, но не доставлен: сотрудник закрыл бота. Напишите ему иначе.",
                kind="bad",
            )
        else:
            w.flash(c, "Ответ отправлен." + (" Сохранён в базу." if entry else ""))
        return w.see_other("/queue")

    @app.post("/queue/{key}/savekb", response_class=HTMLResponse)
    def save_kb_only(
        key: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        question: Annotated[str, Form()] = "",
        answer: Annotated[str, Form()] = "",
        basis: Annotated[str, Form()] = "",
        audience: Annotated[str, Form()] = "all",
    ) -> Response:
        t = d.repo.ticket(c.company, key)
        if t is None or t.confidential:
            return w.error_page(c, "queue", "Обращение не найдено", 404)
        form = {"question": question, "answer": answer, "basis": basis, "audience": audience,
                "text": answer}  # fmt: skip
        try:
            e = d.kbadmin.add(c.company, c.user.email, question, answer, basis, audience, c.now)
        except PanelError as err:
            return queue_page(w, c, key=key, win="savekb", form=form, errors={err.field: str(err)})
        d.journal.add(c.company, c.now, c.user.email, "kb.add", f"#{e.id}")
        w.flash(c, "Ответ сохранён в базу.")
        return w.see_other(f"/queue?k={key}")

    @app.post("/undo/{uid}")
    def undo(
        uid: str, c: Annotated[Ctx, Depends(w.ctx)], back: Annotated[str, Form()] = "/"
    ) -> Response:
        if d.undo.cancel(c.company, uid, c.now):
            w.flash(c, "Действие отменено.")
        else:
            w.flash(c, "Время отмены (10 секунд) вышло.", kind="bad")
        return w.see_other(back if re.fullmatch(r"/[^/\\\s][^\s]*|/", back) else "/")
