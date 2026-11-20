"""П3 «База ответов»: список, поиск, добавить, править, архив, затронутые ответы и пересмотр."""

from typing import Annotated

from fastapi import Depends, FastAPI, Form, Query
from fastapi.responses import HTMLResponse, Response

from hrgid.bot.models import KbEntry
from hrgid.panel.services import PanelError
from hrgid.panel.web_base import Ctx, Web


def kb_page(
    w: Web,
    c: Ctx,
    *,
    tab: str = "active",
    q: str = "",
    win: str = "",
    entry: KbEntry | None = None,
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    step: int = 0,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    entries = d.kbadmin.entries(c.company, archived=(tab == "archive"), query=q)
    review = d.kbadmin.needs_review(c.company)
    total = len(d.repo.kb_entries(c.company))
    queue = d.kbadmin.needs_review(c.company)
    return w.page(
        c, "kb.html", "kb", status, tab=tab, q=q, entries=entries, review=review, total=total,
        win=win, entry=entry, form=form or {}, errors=errors or {}, step=step,
        n_review=len(queue), groups=d.staff.groups(c.company), section_title="База ответов",
        basis_options=w.basis_options(c),
    )  # fmt: skip


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    def _entry(c: Ctx, entry_id: int) -> KbEntry | None:
        try:
            return d.kbadmin.get(c.company, entry_id)
        except PanelError:
            return None

    def _prefill(c: Ctx, src: str) -> dict[str, str]:
        t = d.repo.ticket(c.company, src) if src else None
        if t is None or t.confidential:
            return {}
        reply = next((m.text for m in reversed(t.messages) if m.author == "hr"), "")
        return {"question": t.text[:300], "answer": reply[:900]}

    @app.get("/kb", response_class=HTMLResponse)
    def kb(c: Annotated[Ctx, Depends(w.ctx)], tab: str = "active", q: str = "") -> Response:
        return kb_page(w, c, tab=tab if tab in ("active", "archive", "review") else "active", q=q)

    @app.get("/kb/new", response_class=HTMLResponse)
    def kb_new(
        c: Annotated[Ctx, Depends(w.ctx)],
        q: str = "",
        src: Annotated[str, Query(alias="from")] = "",
    ) -> Response:
        form = _prefill(c, src) or ({"question": q} if q else {})
        return kb_page(w, c, win="new", form=form)

    @app.post("/kb", response_class=HTMLResponse)
    def kb_add(
        c: Annotated[Ctx, Depends(w.ctx)],
        question: Annotated[str, Form()] = "",
        answer: Annotated[str, Form()] = "",
        basis: Annotated[str, Form()] = "",
        audience: Annotated[str, Form()] = "all",
    ) -> Response:
        form = {"question": question, "answer": answer, "basis": basis, "audience": audience}
        try:
            e = d.kbadmin.add(c.company, c.user.name, question, answer, basis, audience, c.now)
        except PanelError as err:
            return kb_page(w, c, win="new", form=form, errors={err.field: str(err)})
        d.journal.add(c.company, c.now, c.user.email, "kb.add", f"#{e.id}")
        w.flash(c, "Ответ добавлен в базу.")
        return w.see_other("/kb")

    @app.get("/kb/{entry_id}/edit", response_class=HTMLResponse)
    def kb_edit(entry_id: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        e = _entry(c, entry_id)
        if e is None:
            return w.error_page(c, "kb", "Ответ не найден", 404)
        form = {
            "question": e.question,
            "answer": e.answer,
            "basis": e.basis,
            "audience": e.audience,
        }
        return kb_page(w, c, win="edit", entry=e, form=form)

    @app.post("/kb/{entry_id}/edit", response_class=HTMLResponse)
    def kb_edit_do(
        entry_id: int,
        c: Annotated[Ctx, Depends(w.ctx)],
        question: Annotated[str, Form()] = "",
        answer: Annotated[str, Form()] = "",
        basis: Annotated[str, Form()] = "",
        audience: Annotated[str, Form()] = "all",
        review: Annotated[str, Form()] = "",
    ) -> Response:
        e = _entry(c, entry_id)
        if e is None:
            return w.error_page(c, "kb", "Ответ не найден", 404)
        form = {"question": question, "answer": answer, "basis": basis, "audience": audience}
        try:
            d.kbadmin.edit(
                c.company, entry_id, c.user.name, question, answer, basis, audience, c.now
            )
        except PanelError as err:
            return kb_page(w, c, win="edit", entry=e, form=form, errors={err.field: str(err)})
        d.journal.add(c.company, c.now, c.user.email, "kb.edit", f"#{entry_id}")
        w.flash(c, "Ответ обновлён.")
        return w.see_other("/kb/review" if review else "/kb")

    @app.get("/kb/{entry_id}/archive", response_class=HTMLResponse)
    def kb_archive_ask(entry_id: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        e = _entry(c, entry_id)
        if e is None:
            return w.error_page(c, "kb", "Ответ не найден", 404)
        return kb_page(w, c, win="archive", entry=e)

    @app.post("/kb/{entry_id}/archive")
    def kb_archive(entry_id: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        if _entry(c, entry_id) is None:
            return w.error_page(c, "kb", "Ответ не найден", 404)
        d.kbadmin.set_archived(c.company, entry_id, True)
        d.journal.add(c.company, c.now, c.user.email, "kb.archive", f"#{entry_id}")
        uid = d.undo.add(c.company, c.now, "kb.archive", {"id": entry_id}, deferred=False)
        w.flash(c, "Ответ в архиве. Его можно вернуть.", undo=uid)
        return w.see_other("/kb")

    @app.post("/kb/{entry_id}/restore")
    def kb_restore(entry_id: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        if _entry(c, entry_id) is None:
            return w.error_page(c, "kb", "Ответ не найден", 404)
        d.kbadmin.set_archived(c.company, entry_id, False)
        d.journal.add(c.company, c.now, c.user.email, "kb.restore", f"#{entry_id}")
        w.flash(c, "Ответ возвращён из архива.")
        return w.see_other("/kb")

    # --- затронутые ответы (П3.4) и пересмотр (П3.5) -----------------------------------------
    @app.get("/kb/affected", response_class=HTMLResponse)
    def kb_affected(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return kb_page(w, c, win="affected")

    @app.post("/kb/law-change", response_class=HTMLResponse)
    def kb_law_change(
        c: Annotated[Ctx, Depends(w.ctx)], article: Annotated[str, Form()] = ""
    ) -> Response:
        if not article.strip():
            return kb_page(w, c, win="affected", errors={"article": "Укажите номер статьи."})
        n = d.kbadmin.mark_law_change(c.company, article, c.now)
        d.journal.add(c.company, c.now, c.user.email, "kb.review", f"ст. {article.strip()}: {n}")
        w.flash(c, f"Помечено «проверить»: {n}." if n else "Ответов с такой статьёй нет.")
        return w.see_other("/kb?tab=review")

    @app.get("/kb/review", response_class=HTMLResponse)
    def kb_review(c: Annotated[Ctx, Depends(w.ctx)], i: int = 0) -> Response:
        rows = d.kbadmin.needs_review(c.company)
        if not rows:
            return kb_page(w, c, win="affected")
        i = max(0, min(i, len(rows) - 1))
        return kb_page(w, c, win="review", entry=rows[i], step=len(rows))

    @app.post("/kb/review/{entry_id}")
    def kb_review_do(
        entry_id: int, c: Annotated[Ctx, Depends(w.ctx)], decision: Annotated[str, Form()] = ""
    ) -> Response:
        if _entry(c, entry_id) is None:
            return w.error_page(c, "kb", "Ответ не найден", 404)
        if decision == "fix":
            return w.see_other(f"/kb/{entry_id}/edit?review=1")
        try:
            d.kbadmin.decide(c.company, entry_id, decision, c.now, c.user.name)
        except PanelError:
            return w.see_other("/kb/review")
        d.journal.add(c.company, c.now, c.user.email, "kb.review", f"#{entry_id}: {decision}")
        if d.kbadmin.needs_review(c.company):
            return w.see_other("/kb/review")
        w.flash(c, "Пересмотр завершён.")
        return w.see_other("/kb")
