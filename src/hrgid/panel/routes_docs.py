"""П4 «Документы» и П5 «Проверка на ТК»."""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response

from hrgid.docs.parse import MAX_BYTES, DocError
from hrgid.docs.report import export_report
from hrgid.docs.service import Version
from hrgid.docs.tkcheck import CheckReport
from hrgid.panel.services import parse_ru_date
from hrgid.panel.web_base import Ctx, Web


@dataclass(frozen=True)
class DocRow:
    doc_id: str
    title: str
    versions: list[Version]
    active: int | None
    report_risks: dict[int, int]  # версия -> открытых рисков


def doc_rows(w: Web, c: Ctx) -> list[DocRow]:
    d = w.d
    rows: list[DocRow] = []
    for doc in d.docs.documents(c.company):
        vs = d.docs.versions(c.company, doc["doc_id"])
        act = d.docs.active_version(c.company, doc["doc_id"], c.today)
        risks: dict[int, int] = {}
        if d.checker is not None:
            for v in vs:
                rep = d.checker.report(c.company, doc["doc_id"], v.number)
                if rep is not None:
                    risks[v.number] = len(rep.open_risks)
        rows.append(DocRow(doc["doc_id"], doc["title"], vs, act.number if act else None, risks))
    return rows


def docs_page(
    w: Web,
    c: Ctx,
    *,
    win: str = "",
    doc_id: str = "",
    number: int = 0,
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    return w.page(
        c, "docs.html", "docs", status, rows=doc_rows(w, c), win=win, doc_id=doc_id, number=number,
        form=form or {}, errors=errors or {}, extra=extra or {}, today=c.today,
        section_title="Документы", can_order=c.role == "admin",
        doc_title=next((x["title"] for x in d.docs.documents(c.company) if x["doc_id"] == doc_id), ""),
    )  # fmt: skip


def check_page(
    w: Web,
    c: Ctx,
    *,
    doc_id: str = "",
    number: int = 0,
    show_all: bool = False,
    win: str = "",
    item: str = "",
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    docs = d.docs.documents(c.company)
    if not doc_id and docs:
        doc_id = docs[0]["doc_id"]
    if doc_id and doc_id not in {x["doc_id"] for x in docs}:
        raise HTTPException(404, "Документ не найден")
    vs = d.docs.versions(c.company, doc_id) if doc_id else []
    if vs and not number:
        number = vs[-1].number
    ver = next((v for v in vs if v.number == number), None)
    rep: CheckReport | None = None
    if ver is not None and d.checker is not None:
        rep = d.checker.report(c.company, doc_id, number)
    act = d.docs.active_version(c.company, doc_id, c.today) if doc_id else None
    cur = next((i for i in (rep.items if rep else []) if i.number == item), None)
    return w.page(
        c, "check.html", "check", status, docs=docs, doc_id=doc_id, vs=vs, ver=ver, number=number,
        rep=rep, show_all=show_all, shown_items=[i for n, i in enumerate(rep.items if rep else []) if show_all or i.verdict in ("risk", "unclear") or n < 8], win=win, item=item, cur=cur, form=form or {},
        errors=errors or {}, active=act.number if act else None, today=c.today,
        section_title="Проверка на ТК", has_checker=d.checker is not None,
        can_accept=c.role == "admin",
    )  # fmt: skip


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    # ===== П4 ====================================================================================
    @app.get("/docs", response_class=HTMLResponse)
    def docs(
        c: Annotated[Ctx, Depends(w.ctx)], w_: Annotated[str, Query(alias="w")] = ""
    ) -> Response:
        return docs_page(w, c, win="upload" if w_ == "upload" else "")

    @app.post("/docs/upload", response_class=HTMLResponse)
    async def docs_upload(
        c: Annotated[Ctx, Depends(w.ctx)],
        file: Annotated[UploadFile | None, File()] = None,
        title: Annotated[str, Form()] = "",
        doc_id: Annotated[str, Form()] = "",
    ) -> Response:
        form = {"title": title, "doc_id": doc_id}
        if file is None or not file.filename:
            return docs_page(
                w, c, win="upload", form=form, errors={"file": "Выберите файл PDF или DOCX."}
            )
        data = await file.read(MAX_BYTES + 1)
        try:
            ver = d.docs.upload(
                c.company, file.filename, data, c.user.email, title.strip(), doc_id or None
            )
        except DocError as e:
            return docs_page(
                w,
                c,
                win="upload_err",
                form=form,
                errors={"file": str(e)},
                extra={"name": file.filename},
            )
        d.journal.add(
            c.company, c.now, c.user.email, "doc.upload", f"{file.filename} · версия {ver.number}"
        )
        w.flash(
            c,
            f"Документ загружен: прочитано {len(ver.items)} пунктов. Теперь нужен приказ и дата начала.",
        )
        return w.see_other(
            f"/docs/{ver.doc_id}/{ver.number}/order" if c.role == "admin" else "/docs"
        )

    def _version(c: Ctx, doc_id: str, number: int) -> Version:
        try:
            return d.docs.version(c.company, doc_id, number)
        except DocError:
            raise HTTPException(404, "Версия не найдена") from None

    @app.get("/docs/{doc_id}/{number}/order", response_class=HTMLResponse)
    def order_form(doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        w.require(c, "doc.order")
        v = _version(c, doc_id, number)
        form = {
            "order_no": v.order_no,
            "order_date": v.order_date.strftime("%d.%m.%Y") if v.order_date else "",
            "effective_from": v.effective_from.strftime("%d.%m.%Y") if v.effective_from else "",
        }
        return docs_page(w, c, win="order", doc_id=doc_id, number=number, form=form)

    @app.post("/docs/{doc_id}/{number}/order", response_class=HTMLResponse)
    def order_do(
        doc_id: str,
        number: int,
        c: Annotated[Ctx, Depends(w.ctx)],
        order_no: Annotated[str, Form()] = "",
        order_date: Annotated[str, Form()] = "",
        effective_from: Annotated[str, Form()] = "",
    ) -> Response:
        w.require(c, "doc.order")
        _version(c, doc_id, number)
        form = {"order_no": order_no, "order_date": order_date, "effective_from": effective_from}
        err: dict[str, str] = {}
        if not order_no.strip():
            err["order_no"] = "Укажите номер приказа."
        od = parse_ru_date(order_date)
        ef = parse_ru_date(effective_from)
        if od is None:
            err["order_date"] = "Укажите дату приказа, например 15.11.2026."
        if ef is None:
            err["effective_from"] = "Укажите дату начала действия."
        elif od is not None and ef < od:
            err["effective_from"] = "Дата начала не может быть раньше даты приказа."
        if err:
            return docs_page(w, c, win="order", doc_id=doc_id, number=number, form=form, errors=err)
        assert od is not None and ef is not None
        try:
            d.docs.set_order(c.company, doc_id, number, order_no, od, ef, c.user.email)
        except DocError as e:
            return docs_page(
                w,
                c,
                win="order",
                doc_id=doc_id,
                number=number,
                form=form,
                errors={"order_no": str(e)},
            )
        d.journal.add(
            c.company, c.now, c.user.email, "doc.order", f"{doc_id} v{number}: № {order_no.strip()}"
        )
        if number > 1:
            title = next(
                (x["title"] for x in d.docs.documents(c.company) if x["doc_id"] == doc_id), ""
            )
            d.kbadmin.mark_by_doc(c.company, title, "Изменилась версия правил компании")
        w.flash(c, "Приказ записан. Версия начнёт действовать с указанной даты.")
        return w.see_other("/docs")

    @app.get("/docs/{doc_id}/compare", response_class=HTMLResponse)
    def compare(doc_id: str, c: Annotated[Ctx, Depends(w.ctx)], a: int = 0, b: int = 0) -> Response:
        vs = d.docs.versions(c.company, doc_id)
        if not vs:
            raise HTTPException(404, "Документ не найден")
        if not b:
            b = vs[-1].number
        if not a:
            a = vs[-2].number if len(vs) > 1 else b
        _version(c, doc_id, a)
        _version(c, doc_id, b)
        rows = d.docs.compare(c.company, doc_id, a, b)
        return docs_page(w, c, win="compare", doc_id=doc_id, extra={"a": a, "b": b, "diff": rows})

    @app.get("/docs/{doc_id}/rollback", response_class=HTMLResponse)
    def rollback_form(doc_id: str, c: Annotated[Ctx, Depends(w.ctx)], to: int = 0) -> Response:
        w.require(c, "doc.rollback")
        _version(c, doc_id, to)
        return docs_page(w, c, win="rollback", doc_id=doc_id, number=to)

    @app.post("/docs/{doc_id}/rollback", response_class=HTMLResponse)
    def rollback_do(
        doc_id: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        to: Annotated[int, Form()],
        reason: Annotated[str, Form()] = "",
    ) -> Response:
        w.require(c, "doc.rollback")
        _version(c, doc_id, to)
        try:
            res = d.docs.rollback(c.company, doc_id, to, reason, c.user.email, c.today)
        except DocError as e:
            fld = "reason" if e.code == "reason" else "to"
            return docs_page(
                w,
                c,
                win="rollback",
                doc_id=doc_id,
                number=to,
                form={"reason": reason},
                errors={fld: str(e)},
            )
        d.journal.add(c.company, c.now, c.user.email, "doc.rollback", f"{doc_id} → v{to}")
        w.flash(c, f"Откат выполнен. Ответов с пометкой «проверить»: {res.affected_answers}.")
        return w.see_other("/docs")

    @app.get("/docs/{doc_id}/{number}/delete", response_class=HTMLResponse)
    def delete_form(doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        w.require(c, "doc.delete_version")
        _version(c, doc_id, number)
        act = d.docs.active_version(c.company, doc_id, c.today)
        win = "delete_deny" if act is not None and act.number == number else "delete"
        return docs_page(w, c, win=win, doc_id=doc_id, number=number)

    @app.post("/docs/{doc_id}/{number}/delete")
    def delete_do(doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        w.require(c, "doc.delete_version")
        _version(c, doc_id, number)
        act = d.docs.active_version(c.company, doc_id, c.today)
        if act is not None and act.number == number:
            return docs_page(w, c, win="delete_deny", doc_id=doc_id, number=number, status=409)
        uid = d.undo.add(
            c.company, c.now, "doc.delete_version",
            {"doc_id": doc_id, "number": number, "user": c.user.email}, deferred=True,
        )  # fmt: skip
        d.journal.add(c.company, c.now, c.user.email, "doc.delete_version", f"{doc_id} v{number}")
        w.flash(c, f"Версия {number} будет удалена через 10 секунд.", undo=uid)
        return w.see_other("/docs")

    @app.get("/docs/{doc_id}/{number}", response_class=HTMLResponse)
    def view_version(doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        _version(c, doc_id, number)
        return docs_page(w, c, win="view", doc_id=doc_id, number=number)

    # ===== П5 ====================================================================================
    @app.get("/check", response_class=HTMLResponse)
    def check(
        c: Annotated[Ctx, Depends(w.ctx)], doc: str = "", v: int = 0, all: int = 0
    ) -> Response:
        return check_page(w, c, doc_id=doc, number=v, show_all=bool(all))

    @app.post("/check/{doc_id}/{number}/run")
    def check_run(doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        if d.checker is None:
            raise HTTPException(503, "Проверка недоступна")
        _version(c, doc_id, number)
        rep = d.checker.check_version(c.company, doc_id, number, c.user.email)
        d.journal.add(c.company, c.now, c.user.email, "tk.check", f"{doc_id} v{number}")
        w.flash(c, f"Проверка готова: рисков — {rep.count('risk')}.")
        return w.see_other(f"/check?doc={doc_id}&v={number}")

    @app.get("/check/{doc_id}/{number}/fix", response_class=HTMLResponse)
    def fix_form(
        doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)], item: str = ""
    ) -> Response:
        page = check_page(w, c, doc_id=doc_id, number=number, win="fix", item=item)
        return page

    @app.post("/check/{doc_id}/{number}/fix", response_class=HTMLResponse)
    def fix_do(
        doc_id: str,
        number: int,
        c: Annotated[Ctx, Depends(w.ctx)],
        item: Annotated[str, Form()] = "",
        text: Annotated[str, Form()] = "",
    ) -> Response:
        if d.checker is None:
            raise HTTPException(503, "Проверка недоступна")
        _version(c, doc_id, number)
        if not text.strip():
            return check_page(
                w,
                c,
                doc_id=doc_id,
                number=number,
                win="fix",
                item=item,
                form={"text": text},
                errors={"text": "Напишите итоговый текст пункта."},
            )
        try:
            nv, rep = d.checker.fix_item(c.company, doc_id, number, item, text, c.user.email)
        except DocError as e:
            return check_page(w, c, doc_id=doc_id, number=number, win="fix", item=item,
                              form={"text": text}, errors={"text": str(e)})  # fmt: skip
        d.journal.add(
            c.company, c.now, c.user.email, "tk.fix", f"{doc_id} п. {item} → v{nv.number}"
        )
        w.flash(
            c,
            f"Создана версия {nv.number} (проект) и проверена заново: рисков — {len(rep.open_risks)}.",
        )
        return w.see_other(f"/check?doc={doc_id}&v={nv.number}")

    @app.get("/check/{doc_id}/{number}/accept", response_class=HTMLResponse)
    def accept_form(
        doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)], item: str = ""
    ) -> Response:
        w.require(c, "tk.accept_risk")
        return check_page(w, c, doc_id=doc_id, number=number, win="accept", item=item)

    @app.post("/check/{doc_id}/{number}/accept", response_class=HTMLResponse)
    def accept_do(
        doc_id: str,
        number: int,
        c: Annotated[Ctx, Depends(w.ctx)],
        item: Annotated[str, Form()] = "",
        comment: Annotated[str, Form()] = "",
        understood: Annotated[str, Form()] = "",
    ) -> Response:
        w.require(c, "tk.accept_risk")
        if d.checker is None:
            raise HTTPException(503, "Проверка недоступна")
        try:
            d.checker.accept_risk(
                c.company, doc_id, number, item, comment, c.user.email, bool(understood)
            )
        except DocError as e:
            fld = {"comment": "comment", "understood": "understood"}.get(e.code, "comment")
            return check_page(w, c, doc_id=doc_id, number=number, win="accept", item=item,
                              form={"comment": comment}, errors={fld: str(e)})  # fmt: skip
        d.journal.add(c.company, c.now, c.user.email, "tk.accept_risk", f"{doc_id} п. {item}")
        w.flash(c, "Риск принят. Автор и комментарий записаны в журнал.")
        return w.see_other(f"/check?doc={doc_id}&v={number}")

    @app.get("/check/{doc_id}/{number}/pdf")
    def pdf(doc_id: str, number: int, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        if d.checker is None:
            raise HTTPException(503, "Проверка недоступна")
        rep = d.checker.report(c.company, doc_id, number)
        if rep is None:
            raise HTTPException(404, "Версия ещё не проверена")
        title = next(
            (x["title"] for x in d.docs.documents(c.company) if x["doc_id"] == doc_id), doc_id
        )
        ver = d.docs.version(c.company, doc_id, number)
        note = "" if ver.applicable(c.today) else "Версия — проект, ещё не утверждена приказом."
        with tempfile.TemporaryDirectory() as tmp:
            path, fmt = export_report(rep, title, Path(tmp), note)
            data = path.read_bytes()
        d.journal.add(c.company, c.now, c.user.email, "tk.pdf", f"{doc_id} v{number}")
        mime = "application/pdf" if fmt == "pdf" else "text/html; charset=utf-8"
        return Response(
            data, media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="tk-v{number}.{fmt}"'},
        )  # fmt: skip
