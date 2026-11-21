"""П6 "Employees" (invitation, 1C import, codes, groups, profile) and П11 "Announcements"."""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from hrgid.bot.models import Announcement, Button, Employee, Out
from hrgid.panel import imports
from hrgid.panel.services import PanelError
from hrgid.panel.web_base import Ctx, Web

ANN_TITLE_MAX, ANN_TEXT_MAX = 80, 900


@dataclass(frozen=True)
class StaffRow:
    emp: Employee
    state: str  # connected | code | nocode | disabled
    label: str


def staff_rows(w: Web, c: Ctx) -> list[StaffRow]:
    rows: list[StaffRow] = []
    for e in sorted(w.d.repo.employees(c.company), key=lambda x: x.full_name):
        if e.status == "disabled":
            rows.append(StaffRow(e, "disabled", "Доступ отключён"))
        elif e.telegram_id is not None:
            rows.append(StaffRow(e, "connected", "Подключён"))
        elif any(k.status == "active" for k in w.d.repo.codes_of(c.company, e.id)):
            rows.append(StaffRow(e, "code", "Код выдан, ждём вход"))
        else:
            rows.append(StaffRow(e, "nocode", "Нет кода"))
    return rows


def staff_page(
    w: Web,
    c: Ctx,
    *,
    win: str = "",
    emp: Employee | None = None,
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    rows = staff_rows(w, c)
    asof = max(
        (e.profile_date for e in d.repo.employees(c.company) if e.profile_date), default=None
    )
    prof: dict[str, Any] = {}
    if emp is not None:
        prof = {
            "tickets": d.staff.ticket_count(c.company, emp.id),
            "codes": [k for k in d.repo.codes_of(c.company, emp.id) if k.status == "active"],
        }
    return w.page(
        c, "staff.html", "staff", status, rows=rows, asof=asof, win=win, emp=emp, form=form or {},
        errors=errors or {}, extra=extra or {}, prof=prof, groups=d.staff.groups(c.company),
        sizes=d.staff.group_sizes(c.company), section_title="Сотрудники", fields=imports.FIELDS,
        required=imports.REQUIRED,
    )  # fmt: skip


def recipients(w: Web, c: Ctx, groups: list[str]) -> list[Employee]:
    return [
        e for e in w.d.repo.employees(c.company)
        if e.status == "active" and e.telegram_id is not None and e.consent_at is not None
        and (not groups or e.group in groups)
    ]  # fmt: skip


def ann_page(
    w: Web,
    c: Ctx,
    *,
    win: str = "",
    ann: Announcement | None = None,
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    status: int = 200,
    compose: bool = False,
) -> HTMLResponse:
    d = w.d
    items = sorted(d.repo.announcements(c.company), key=lambda a: a.created_at, reverse=True)
    stats = {a.id: (len(a.reads), len(a.recipients)) for a in items}
    return w.page(
        c, "ann.html", "ann", status, items=items, stats=stats, win=win, ann=ann, form=form or {},
        errors=errors or {}, extra=extra or {}, groups=d.staff.groups(c.company),
        section_title="Объявления",
        compose=compose,
        n_all=len(recipients(w, c, [])),
    )  # fmt: skip


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    def _emp(c: Ctx, emp_id: str) -> Employee:
        e = d.repo.employee(c.company, emp_id)
        if e is None:
            raise HTTPException(404, "Сотрудник не найден")
        return e

    # ===== list ====================================================================================
    @app.get("/staff", response_class=HTMLResponse)
    def staff(
        c: Annotated[Ctx, Depends(w.ctx)], w_: Annotated[str, Query(alias="w")] = ""
    ) -> Response:
        win = {"invite": "invite", "import1": "import1", "codes": "codes", "groups": "groups"}.get(
            w_, ""
        )
        return staff_page(w, c, win=win)

    # ===== invitation ======================================================================================
    @app.post("/staff/invite", response_class=HTMLResponse)
    def invite(
        c: Annotated[Ctx, Depends(w.ctx)],
        full_name: Annotated[str, Form()] = "",
        hire_date: Annotated[str, Form()] = "",
        position: Annotated[str, Form()] = "",
        group: Annotated[str, Form()] = "",
        schedule: Annotated[str, Form()] = "",
    ) -> Response:
        form = {"full_name": full_name, "hire_date": hire_date, "position": position,
                "group": group, "schedule": schedule}  # fmt: skip
        try:
            e, code = d.staff.invite(
                c.company,
                full_name,
                hire_date,
                c.now,
                position=position,
                group=group,
                schedule=schedule,
            )
        except PanelError as err:
            extra = {"dup_id": err.extra.get("dup_id", "")}
            return staff_page(
                w, c, win="invite", form=form, errors={err.field: str(err)}, extra=extra
            )
        d.journal.add(c.company, c.now, c.user.email, "emp.invite", e.full_name)
        return staff_page(w, c, win="code", emp=e, extra={"code": code, "bot": d.bot_username})

    # ===== new code ======================================================================================
    @app.get("/staff/{emp_id}/newcode", response_class=HTMLResponse)
    def newcode_form(emp_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return staff_page(w, c, win="newcode", emp=_emp(c, emp_id))

    @app.post("/staff/{emp_id}/newcode", response_class=HTMLResponse)
    def newcode_do(
        emp_id: str, c: Annotated[Ctx, Depends(w.ctx)], reason: Annotated[str, Form()] = ""
    ) -> Response:
        e = _emp(c, emp_id)
        try:
            code = d.staff.issue_code(c.company, emp_id, c.now)
        except PanelError as err:
            return staff_page(w, c, win="newcode", emp=e, errors={"employee": str(err)})
        d.journal.add(c.company, c.now, c.user.email, "emp.newcode", e.full_name)
        return staff_page(
            w, c, win="code", emp=e, extra={"code": code, "bot": d.bot_username, "again": True}
        )

    # ===== deactivation =======================================================================================
    @app.get("/staff/{emp_id}/disable", response_class=HTMLResponse)
    def disable_form(emp_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return staff_page(w, c, win="disable", emp=_emp(c, emp_id))

    @app.post("/staff/{emp_id}/disable", response_class=HTMLResponse)
    def disable_do(
        emp_id: str, c: Annotated[Ctx, Depends(w.ctx)], confirm: Annotated[str, Form()] = ""
    ) -> Response:
        e = _emp(c, emp_id)
        try:
            d.staff.disable(c.company, emp_id, no_longer_works=bool(confirm))
        except PanelError as err:
            return staff_page(w, c, win="disable", emp=e, errors={err.field: str(err)})
        d.journal.add(c.company, c.now, c.user.email, "emp.disable", e.full_name)
        uid = d.undo.add(c.company, c.now, "emp.disable", {"id": emp_id}, deferred=False)
        w.flash(c, "Доступ отключён. Бот больше не отвечает, история хранится по сроку.", undo=uid)
        return w.see_other("/staff")

    @app.post("/staff/{emp_id}/enable")
    def enable_do(emp_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        e = _emp(c, emp_id)
        d.staff.enable(c.company, emp_id)
        d.journal.add(c.company, c.now, c.user.email, "emp.enable", e.full_name)
        w.flash(c, "Доступ возвращён. Выдайте новый код, если нужно.")
        return w.see_other("/staff")

    # ===== profile, editing, data deletion =====================================================================
    @app.get("/staff/{emp_id}", response_class=HTMLResponse)
    def profile(emp_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return staff_page(w, c, win="profile", emp=_emp(c, emp_id))

    @app.get("/staff/{emp_id}/edit", response_class=HTMLResponse)
    def edit_form(emp_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        e = _emp(c, emp_id)
        form = {
            "hire_date": e.hire_date.strftime("%d.%m.%Y") if e.hire_date else "",
            "schedule": e.schedule, "salary": str(e.salary or ""), "group": e.group,
            "position": e.position,
        }  # fmt: skip
        return staff_page(w, c, win="edit", emp=e, form=form)

    @app.post("/staff/{emp_id}/edit", response_class=HTMLResponse)
    def edit_do(
        emp_id: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        hire_date: Annotated[str, Form()] = "",
        schedule: Annotated[str, Form()] = "",
        salary: Annotated[str, Form()] = "",
        group: Annotated[str, Form()] = "",
        position: Annotated[str, Form()] = "",
    ) -> Response:
        e = _emp(c, emp_id)
        form = {"hire_date": hire_date, "schedule": schedule, "salary": salary, "group": group,
                "position": position}  # fmt: skip
        try:
            d.staff.edit(c.company, emp_id, hire_date=hire_date, schedule=schedule, salary=salary,
                         group=group, position=position, today=c.today)  # fmt: skip
        except PanelError as err:
            return staff_page(w, c, win="edit", emp=e, form=form, errors={err.field: str(err)})
        d.journal.add(c.company, c.now, c.user.email, "emp.edit", e.full_name)
        w.flash(c, "Данные исправлены. Следующий импорт из 1С перезапишет их.")
        return w.see_other(f"/staff/{emp_id}")

    @app.get("/staff/{emp_id}/purge", response_class=HTMLResponse)
    def purge_form(emp_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return staff_page(w, c, win="purge", emp=_emp(c, emp_id))

    @app.post("/staff/{emp_id}/purge", response_class=HTMLResponse)
    def purge_do(
        emp_id: str,
        c: Annotated[Ctx, Depends(w.ctx)],
        reason: Annotated[str, Form()] = "",
        understood: Annotated[str, Form()] = "",
    ) -> Response:
        e = _emp(c, emp_id)
        err: dict[str, str] = {}
        if not reason.strip():
            err["reason"] = "Укажите основание: например, запрос сотрудника."
        if not understood:
            err["understood"] = "Отметьте «понимаю»: удаление нельзя вернуть."
        if err:
            return staff_page(w, c, win="purge", emp=e, form={"reason": reason}, errors=err)
        uid = d.undo.add(c.company, c.now, "emp.purge", {"id": emp_id}, deferred=True)
        d.journal.add(c.company, c.now, c.user.email, "emp.purge", e.full_name)
        w.flash(c, "Данные сотрудника будут удалены через 10 секунд.", undo=uid)
        return w.see_other("/staff")

    # ===== groups =======================================================================================
    @app.get("/staff/groups/list", response_class=HTMLResponse)
    def groups(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return staff_page(w, c, win="groups")

    @app.post("/staff/groups/add", response_class=HTMLResponse)
    def group_add(c: Annotated[Ctx, Depends(w.ctx)], name: Annotated[str, Form()] = "") -> Response:
        try:
            n = d.staff.add_group(c.company, name)
        except PanelError as err:
            return staff_page(w, c, win="groups", form={"name": name}, errors={"name": str(err)})
        d.journal.add(c.company, c.now, c.user.email, "group.add", n)
        w.flash(c, "Группа добавлена.")
        return w.see_other("/staff/groups/list")

    @app.post("/staff/groups/delete", response_class=HTMLResponse)
    def group_delete(
        c: Annotated[Ctx, Depends(w.ctx)], name: Annotated[str, Form()] = ""
    ) -> Response:
        try:
            d.staff.delete_group(c.company, name)
        except PanelError as err:
            return staff_page(w, c, win="groups", errors={"name": str(err)})
        d.journal.add(c.company, c.now, c.user.email, "group.delete", name)
        w.flash(c, "Группа удалена.")
        return w.see_other("/staff/groups/list")

    # ===== 1C import =========================================================================================
    @app.post("/staff/import", response_class=HTMLResponse)
    async def import_upload(
        c: Annotated[Ctx, Depends(w.ctx)], file: Annotated[UploadFile | None, File()] = None
    ) -> Response:
        if file is None or not file.filename:
            return staff_page(w, c, win="import1", errors={"file": "Выберите файл CSV или XLSX."})
        data = await file.read(imports.MAX_BYTES + 1)
        try:
            token = d.staff.import_start(c.company, file.filename, data)
        except imports.ImportError_ as e:
            return staff_page(
                w, c, win="import1_err", errors={"file": str(e)}, extra={"name": file.filename}
            )
        return w.see_other(f"/staff/import/{token}/map")

    def _imp(c: Ctx, token: str) -> dict[str, Any]:
        try:
            return d.staff.import_doc(c.company, token)
        except PanelError:
            raise HTTPException(404, "Импорт не найден") from None

    @app.get("/staff/import/{token}/map", response_class=HTMLResponse)
    def import_map(token: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        doc = _imp(c, token)
        return staff_page(w, c, win="import2", extra={"token": token, "headers": doc["headers"],
                                                      "mapping": doc["mapping"]})  # fmt: skip

    @app.post("/staff/import/{token}/map", response_class=HTMLResponse)
    async def import_map_do(
        token: str, request: Request, c: Annotated[Ctx, Depends(w.ctx)]
    ) -> Response:
        doc = _imp(c, token)
        raw = await request.form()
        mapping: dict[str, int] = {}
        for f in imports.FIELDS:
            v = str(raw.get(f, ""))
            if v.isdigit():
                mapping[f] = int(v)
        try:
            d.staff.import_set_mapping(c.company, token, mapping)
        except PanelError as err:
            return staff_page(
                w, c, win="import2", errors={err.field: str(err)},
                extra={"token": token, "headers": doc["headers"], "mapping": mapping},
            )  # fmt: skip
        return w.see_other(f"/staff/import/{token}/preview")

    @app.get("/staff/import/{token}/preview", response_class=HTMLResponse)
    def import_preview(token: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        _imp(c, token)
        plan = d.staff.import_plan(c.company, token)
        return staff_page(w, c, win="import3", extra={"token": token, "plan": plan})

    @app.get("/staff/import/{token}/errors", response_class=HTMLResponse)
    def import_errors(token: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        _imp(c, token)
        plan = d.staff.import_plan(c.company, token)
        return staff_page(w, c, win="import_errors", extra={"token": token, "plan": plan})

    @app.post("/staff/import/{token}/apply", response_class=HTMLResponse)
    async def import_apply(
        token: str, request: Request, c: Annotated[Ctx, Depends(w.ctx)]
    ) -> Response:
        _imp(c, token)
        raw = await request.form()
        disable_ids = [str(v) for v in raw.getlist("disable")]
        res = d.staff.import_apply(c.company, token, disable_ids, c.now, c.today)
        d.journal.add(c.company, c.now, c.user.email, "emp.import",
                      f"новых {res.created}, обновлено {res.updated}, отключено {res.disabled}")  # fmt: skip
        return staff_page(w, c, win="import4", extra={"res": res})

    # ===== mass issue of codes ===================================================================================
    @app.post("/staff/codes", response_class=HTMLResponse)
    def codes(
        c: Annotated[Ctx, Depends(w.ctx)],
        who: Annotated[str, Form()] = "new",
        fmt: Annotated[str, Form()] = "csv",
    ) -> Response:
        rows = d.staff.issue_codes(c.company, "all" if who == "all" else "new", c.now)
        if not rows:
            w.flash(c, "Некому выдавать: у всех уже есть код или Telegram.", kind="bad")
            return w.see_other("/staff")
        d.journal.add(c.company, c.now, c.user.email, "emp.codes", f"{len(rows)} шт.")
        if fmt == "print":
            items = [
                (e, code, d.staff.qr_svg(f"https://t.me/{d.bot_username}?start={code}"))
                for e, code in rows
            ]
            return w.public(c.request, "codes_sheet.html", items=items, bot=d.bot_username,
                            company=c.settings.company_name)  # fmt: skip
        return Response(
            d.staff.codes_csv(rows).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="codes.csv"'},
        )

    @app.get("/staff/import/sample.csv")
    def sample(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        txt = "ФИО;Дата приёма;Должность;График;Оклад;Группа;Заработок за 12 месяцев\n"
        txt += "Иванов Иван Иванович;11.03.2024;дизайнер;5/2;80000;Дизайн;960000\n"
        return Response(("﻿" + txt).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="sample.csv"'})  # fmt: skip

    # ===== П11 "Announcements" =========================================================================================
    @app.get("/announcements", response_class=HTMLResponse)
    def announcements(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return ann_page(w, c)

    @app.get("/announcements/new", response_class=HTMLResponse)
    def ann_new(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        draft = d.store.get("pdraft", c.company, c.user.email) or {}
        return ann_page(w, c, form={k: str(v) for k, v in draft.items()}, compose=True)

    def _ann_form(title: str, text: str, groups: list[str], asks: str) -> dict[str, str]:
        return {"title": title, "text": text, "groups": "|".join(groups), "asks": asks}

    def _ann_check(title: str, text: str, n: int) -> dict[str, str]:
        err: dict[str, str] = {}
        if not title.strip():
            err["title"] = "Напишите заголовок."
        elif len(title.strip()) > ANN_TITLE_MAX:
            err["title"] = f"Заголовок — не длиннее {ANN_TITLE_MAX} знаков."
        if not text.strip():
            err["text"] = "Напишите текст объявления."
        elif len(text.strip()) > ANN_TEXT_MAX:
            err["text"] = f"Текст — не длиннее {ANN_TEXT_MAX} знаков."
        if n == 0:
            err["groups"] = "Нет получателей: в выбранных группах никто не подключил бота."
        return err

    @app.post("/announcements/preview", response_class=HTMLResponse)
    async def ann_preview(request: Request, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        raw = await request.form()
        title, text = str(raw.get("title", "")), str(raw.get("text", ""))
        groups = [str(g) for g in raw.getlist("groups")]
        asks = str(raw.get("asks", ""))
        form = _ann_form(title, text, groups, asks)
        if raw.get("draft"):
            d.store.put("pdraft", c.company, c.user.email, {"title": title, "text": text,
                                                              "asks": asks})  # fmt: skip
            w.flash(c, "Черновик сохранён.")
            return w.see_other("/announcements")
        n = len(recipients(w, c, groups))
        err = _ann_check(title, text, n)
        if err:
            return ann_page(w, c, form=form, errors=err, compose=True)
        return ann_page(w, c, win="confirm", form=form, extra={"n": n})

    @app.post("/announcements/send", response_class=HTMLResponse)
    async def ann_send(request: Request, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        raw = await request.form()
        title, text = str(raw.get("title", "")).strip(), str(raw.get("text", "")).strip()
        groups = [g for g in str(raw.get("groups", "")).split("|") if g]
        asks = bool(raw.get("asks"))
        n = len(recipients(w, c, groups))
        err = _ann_check(title, text, n)
        if err:
            return ann_page(
                w,
                c,
                win="",
                form=_ann_form(title, text, groups, str(raw.get("asks", ""))),
                errors=err,
            )
        a = d.desk.publish_announcement(
            c.company, title, text, c.user.name, c.now, asks_dates=asks, groups=groups
        )
        d.store.delete("pdraft", c.company, c.user.email)
        d.journal.add(
            c.company, c.now, c.user.email, "ann.send", f"{title} · {len(a.recipients)} получ."
        )
        w.flash(c, f"Объявление отправлено: {len(a.recipients)} получателей.")
        return w.see_other(f"/announcements/{a.id}")

    def _ann(c: Ctx, ann_id: str) -> Announcement:
        a = d.repo.announcement(c.company, ann_id)
        if a is None:
            raise HTTPException(404, "Объявление не найдено")
        return a

    @app.get("/announcements/{ann_id}", response_class=HTMLResponse)
    def ann_readers(
        ann_id: str, c: Annotated[Ctx, Depends(w.ctx)], tab: str = "unread"
    ) -> Response:
        a = _ann(c, ann_id)
        emps = {e.id: e for e in d.repo.employees(c.company)}
        read = [(emps[i].full_name, at) for i, at in a.reads.items() if i in emps]
        return ann_page(
            w, c, win="readers", ann=a,
            extra={"tab": tab, "unread": d.desk.unread(c.company, ann_id), "read": read},
        )  # fmt: skip

    @app.get("/announcements/{ann_id}/remind", response_class=HTMLResponse)
    def ann_remind_ask(ann_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        a = _ann(c, ann_id)
        return ann_page(
            w, c, win="remind", ann=a, extra={"unread": d.desk.unread(c.company, ann_id)}
        )

    @app.post("/announcements/{ann_id}/remind")
    def ann_remind(ann_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        a = _ann(c, ann_id)
        sent = 0
        for e in d.repo.employees(c.company):
            if e.id in a.reads or e.telegram_id is None or e.status != "active":
                continue
            if a.recipients and e.id not in a.recipients:
                continue
            d.repo.enqueue(
                Out(chat_id=e.telegram_id, company_id=c.company,
                    text=f"Напоминание: прочитайте объявление «{a.title}».",
                    rows=[[Button(text="Прочитано", data=f"ann:read:{a.id}")]]),
                c.now,
            )  # fmt: skip
            sent += 1
        d.repo.save_announcement(a.model_copy(update={"reminded_at": c.now}))
        d.journal.add(c.company, c.now, c.user.email, "ann.remind", f"{a.title} · {sent}")
        w.flash(c, f"Напоминание отправлено: {sent}.")
        return w.see_other(f"/announcements/{ann_id}")
