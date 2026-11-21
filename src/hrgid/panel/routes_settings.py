"""П9 «Настройки» (только администратор) и П10 «Журнал»."""

import csv
import html
import io
from datetime import date, time, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from hrgid.mcp.tokens import McpTokens
from hrgid.panel.journal import LABELS, Entry
from hrgid.panel.ops import FOREIGN, OVER_ACTIONS, PROVIDER_NAMES
from hrgid.panel.services import K_REQ, PanelError
from hrgid.panel.web_base import COOKIE, Ctx, Web

RESULTS = {
    "ok": "Вошёл", "bad_code": "Код неверный", "blocked": "Вход закрыт на 15 минут",
    "code_sent": "Код отправлен", "logout": "Вышел", "session_expired": "Сессия закрыта",
    "no_access": "Почты нет в списке",
}  # fmt: skip
FONT = Path("C:/Windows/Fonts/arial.ttf")


def _hm(s: str) -> time | None:
    try:
        h, m = s.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def settings_page(
    w: Web,
    c: Ctx,
    *,
    win: str = "",
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    extra: dict[str, object] | None = None,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    s = c.settings
    tech = d.ops.tech(c.company)
    base = {
        "hours_start": s.hours_start.strftime("%H:%M"), "hours_end": s.hours_end.strftime("%H:%M"),
        "sla_hours": str(s.sla_hours), "urgent_contact": s.urgent_contact,
        "greeting": tech.greeting, "escalation": tech.escalation,
        "retention_days": str(tech.retention_days), "utc_offset": str(s.utc_offset),
    }  # fmt: skip
    requests = d.store.all(K_REQ, c.company)
    return w.page(
        c, "settings.html", "settings", status, win=win, form={**base, **(form or {})},
        errors=errors or {}, extra=extra or {}, tech=tech, providers=PROVIDER_NAMES,
        mcp_tokens=McpTokens(d.store, d.clock).list(c.company) if win in ("tokens", "token_new") else [],
        requests=list(reversed(requests))[:5], over_actions=OVER_ACTIONS, section_title="Настройки",
    )  # fmt: skip


def journal_page(
    w: Web,
    c: Ctx,
    *,
    tab: str = "actions",
    who: str = "",
    kind: str = "",
    since: str = "",
    until: str = "",
    win: str = "",
    entry: Entry | None = None,
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    rows = d.journal.entries(c.company)
    filtered = bool(who or kind or since or until)
    if who:
        rows = [e for e in rows if e.user == who]
    if kind:
        rows = [e for e in rows if e.action == kind]
    sd = _date(since)
    ud = _date(until)
    if sd:
        rows = [e for e in rows if c.local(e.at).date() >= sd]
    if ud:
        rows = [e for e in rows if c.local(e.at).date() <= ud]
    logins = d.auth.login_log(c.company) if c.role == "admin" else []
    return w.page(
        c, "journal.html", "journal", status, tab=tab, rows=rows[:200], total=len(rows),
        filtered=filtered, who=who, kind=kind, since=since, until=until, win=win, entry=entry,
        logins=logins, results=RESULTS, labels=LABELS, users=d.auth.users(c.company),
        form=form or {}, errors=errors or {}, section_title="Журнал",
    )  # fmt: skip


def _date(s: str) -> date | None:
    from hrgid.panel.imports import parse_date

    return parse_date(s) if s.strip() else None


def _rows_pdf(title: str, lines: list[str]) -> tuple[bytes, str]:
    """PDF журнала; если собрать нельзя — HTML (формат «html»)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate

        font = "Helvetica"
        for f in (FONT, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
            if f.exists():
                pdfmetrics.registerFont(TTFont("PanelFont", str(f)))
                font = "PanelFont"
                break
        st = ParagraphStyle("p", fontName=font, fontSize=9, leading=12)
        buf = io.BytesIO()
        SimpleDocTemplate(buf, pagesize=A4).build(
            [Paragraph(html.escape(title), st)] + [Paragraph(html.escape(x), st) for x in lines]
        )
        return buf.getvalue(), "pdf"
    except Exception:
        body = "<br>".join(html.escape(x) for x in lines)
        return (
            f"<!doctype html><meta charset=utf-8><h1>{html.escape(title)}</h1>{body}".encode(),
            "html",
        )


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    def _admin_only(c: Ctx) -> Response | None:
        if c.role != "admin":
            return w.page(
                c, "state.html", "settings", 403, kind="noaccess", title="Только для администратора", head_title="Настройки", subtitle=f"Для всей компании «{c.settings.company_name}»",
                text="Этот раздел только для администратора компании. Попросите его изменить нужное.",
                ask_link="/costs/ask", section_title="Настройки",
            )  # fmt: skip
        return None

    # ===== П9 ====================================================================================
    @app.get("/settings", response_class=HTMLResponse)
    def settings(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return _admin_only(c) or settings_page(w, c)

    @app.post("/settings", response_class=HTMLResponse)
    def settings_save(
        c: Annotated[Ctx, Depends(w.ctx)],
        hours_start: Annotated[str, Form()] = "",
        hours_end: Annotated[str, Form()] = "",
        sla_hours: Annotated[str, Form()] = "",
        urgent_contact: Annotated[str, Form()] = "",
        greeting: Annotated[str, Form()] = "",
        escalation: Annotated[str, Form()] = "",
        retention_days: Annotated[str, Form()] = "",
        utc_offset: Annotated[str, Form()] = "3",
        provider: Annotated[str, Form()] = "",
        budget_rub: Annotated[str, Form()] = "",
        over_action: Annotated[str, Form()] = "",
    ) -> Response:
        if (deny := _admin_only(c)) is not None:
            return deny
        form = {
            "hours_start": hours_start, "hours_end": hours_end, "sla_hours": sla_hours,
            "urgent_contact": urgent_contact, "greeting": greeting, "escalation": escalation,
            "retention_days": retention_days, "utc_offset": utc_offset,
        }  # fmt: skip
        err: dict[str, str] = {}
        t0, t1 = _hm(hours_start), _hm(hours_end)
        if t0 is None:
            err["hours_start"] = "Время вида 10:00."
        if t1 is None:
            err["hours_end"] = "Время вида 19:00."
        if t0 and t1 and t1 <= t0:
            err["hours_end"] = "Конец рабочего дня раньше начала."
        if not urgent_contact.strip():
            err["urgent_contact"] = "Укажите срочный контакт: его увидит сотрудник вне часов HR."
        if not (sla_hours.isdigit() and 1 <= int(sla_hours) <= 72):
            err["sla_hours"] = "Срок ответа — от 1 до 72 рабочих часов."
        if not (retention_days.isdigit() and 30 <= int(retention_days) <= 3650):
            err["retention_days"] = "Срок хранения — от 30 дней до 10 лет."
        if not (utc_offset.lstrip("-").isdigit() and -12 <= int(utc_offset) <= 14):
            err["utc_offset"] = "Часовой пояс — число часов от UTC."
        if err:
            return settings_page(w, c, form=form, errors=err)
        assert t0 is not None and t1 is not None
        s = c.settings.model_copy(
            update={
                "hours_start": t0, "hours_end": t1, "sla_hours": int(sla_hours),
                "urgent_contact": urgent_contact.strip(), "utc_offset": int(utc_offset),
            }
        )  # fmt: skip
        d.repo.save_settings(s)
        t = d.ops.tech(c.company).model_copy(
            update={
                "greeting": greeting.strip(),
                "escalation": escalation.strip(),
                "retention_days": int(retention_days),
            }  # fmt: skip
        )
        if budget_rub.isdigit() and int(budget_rub) >= 1000:
            t = t.model_copy(update={"budget_rub": float(budget_rub)})
        if over_action in OVER_ACTIONS:
            t = t.model_copy(
                update={"over_action": "queue" if over_action == "queue" else "refuse"}
            )
        d.ops.save_tech(c.company, t)
        d.journal.add(c.company, c.now, c.user.email, "settings.save", "")
        if provider in PROVIDER_NAMES and provider != t.provider:
            if provider in FOREIGN:  # за границу — только через предупреждение (П9.5)
                return settings_page(w, c, win="foreign", form={"provider": provider})
            d.ops.save_tech(
                c.company, t.model_copy(update={"provider": provider, "claude_ack": False})
            )
            d.journal.add(
                c.company, c.now, c.user.email, "settings.model", PROVIDER_NAMES[provider]
            )
        w.flash(c, "Настройки сохранены.")
        return w.see_other("/settings")

    # --- модель (П9.5) ------------------------------------------------------------------------
    @app.get("/settings/model", response_class=HTMLResponse)
    def model_form(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return _admin_only(c) or settings_page(
            w, c, win="model", form={"provider": d.ops.tech(c.company).provider}
        )

    @app.post("/settings/model", response_class=HTMLResponse)
    def model_do(
        c: Annotated[Ctx, Depends(w.ctx)],
        provider: Annotated[str, Form()] = "gigachat",
        ack: Annotated[str, Form()] = "",
        confirm_claude: Annotated[str, Form()] = "",
    ) -> Response:
        if (deny := _admin_only(c)) is not None:
            return deny
        if provider not in PROVIDER_NAMES:
            return settings_page(
                w, c, win="model", errors={"provider": "Выберите модель из списка."}
            )
        if provider in FOREIGN and not confirm_claude:
            return settings_page(w, c, win="foreign", form={"provider": provider})
        if provider in FOREIGN and not ack:
            return settings_page(
                w, c, win="foreign", form={"provider": provider},
                errors={"ack": "Без отметки «Сотрудники уведомлены» включить эту модель нельзя."},
            )  # fmt: skip
        t = d.ops.tech(c.company).model_copy(
            update={"provider": provider, "claude_ack": provider in FOREIGN}
        )
        d.ops.save_tech(c.company, t)
        d.journal.add(c.company, c.now, c.user.email, "settings.model", PROVIDER_NAMES[provider])
        w.flash(c, f"Модель изменена: {PROVIDER_NAMES[provider]}.")
        return w.see_other("/settings")

    # --- токен для ИИ-агентов (П9.6): MCP-сервер, SPEC-09 §10 --------------------------------------
    @app.get("/settings/tokens", response_class=HTMLResponse)
    def tokens_form(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return _admin_only(c) or settings_page(w, c, win="tokens")

    @app.post("/settings/tokens", response_class=HTMLResponse)
    def tokens_issue(
        c: Annotated[Ctx, Depends(w.ctx)], label: Annotated[str, Form()] = ""
    ) -> Response:
        if (deny := _admin_only(c)) is not None:
            return deny
        if len(label.strip()) > 60:
            return settings_page(
                w, c, win="tokens", form={"label": label},
                errors={"label": "Название — не длиннее 60 знаков."},
            )  # fmt: skip
        token, rec = McpTokens(d.store, d.clock).issue(c.company, label, c.user.email)
        d.journal.add(c.company, c.now, c.user.email, "mcp.issue", rec["label"])
        return settings_page(w, c, win="token_new", extra={"token": token, "rec": rec})

    @app.post("/settings/tokens/{token_id}/revoke", response_class=HTMLResponse)
    def tokens_revoke(token_id: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        if (deny := _admin_only(c)) is not None:
            return deny
        if McpTokens(d.store, d.clock).revoke(c.company, token_id, c.user.email):
            d.journal.add(c.company, c.now, c.user.email, "mcp.revoke", token_id)
            w.flash(c, "Токен отозван: ИИ-агент с ним больше не получит данных.")
        return w.see_other("/settings/tokens")

    # --- выгрузка (П9.2) --------------------------------------------------------------------------
    @app.get("/settings/export", response_class=HTMLResponse)
    def export_form(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return _admin_only(c) or settings_page(w, c, win="export")

    @app.post("/settings/export", response_class=HTMLResponse)
    def export_do(
        c: Annotated[Ctx, Depends(w.ctx)], since: Annotated[str, Form()] = ""
    ) -> Response:
        if (deny := _admin_only(c)) is not None:
            return deny
        sd = _date(since)
        if since.strip() and sd is None:
            return settings_page(
                w, c, win="export", form={"since": since}, errors={"since": "Дата вида 01.01.2026."}
            )
        data = d.company.export_zip(c.company, sd)
        token = d.company.stash_export(c.company, data, c.now)
        d.journal.add(c.company, c.now, c.user.email, "company.export", "")
        d.auth.mailer.send(
            c.user.email, "Выгрузка данных компании готова",
            f"Файл выгрузки: /settings/export/{token}\nСсылка действует 24 часа, нужен вход в панель.",
        )  # fmt: skip
        return settings_page(w, c, win="export_ready", extra={"token": token, "size": len(data)})

    @app.get("/settings/export/{token}")
    def export_get(token: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        w.require(c, "company.export")
        data = d.company.fetch_export(c.company, token, c.now)
        if data is None:
            raise HTTPException(404, "Файл не найден или ссылка устарела")
        return Response(
            data, media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="company-data.zip"'},
        )  # fmt: skip

    # --- удаление данных компании (П9.3) -------------------------------------------------------------
    @app.get("/settings/delete", response_class=HTMLResponse)
    def delete_form(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return _admin_only(c) or settings_page(w, c, win="delete1")

    @app.post("/settings/delete", response_class=HTMLResponse)
    def delete_step1(
        c: Annotated[Ctx, Depends(w.ctx)], exported: Annotated[str, Form()] = ""
    ) -> Response:
        if (deny := _admin_only(c)) is not None:
            return deny
        if not exported:
            return settings_page(
                w, c, win="delete1", errors={"exported": "Сначала выгрузите данные и отметьте это."}
            )
        return settings_page(w, c, win="delete2")

    @app.post("/settings/delete/confirm", response_class=HTMLResponse)
    def delete_step2(
        c: Annotated[Ctx, Depends(w.ctx)], name: Annotated[str, Form()] = ""
    ) -> Response:
        if (deny := _admin_only(c)) is not None:
            return deny
        w.require(c, "company.delete")
        try:
            d.company.delete_company(c.company, name, True)
            d.store.put("pgone", "", c.company, {"at": c.now.isoformat(), "by": c.user.email})
        except PanelError as err:
            return settings_page(w, c, win="delete2", errors={err.field: str(err)})
        resp = w.see_other("/login")
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ===== П10 =====================================================================================
    @app.get("/journal", response_class=HTMLResponse)
    def journal(
        c: Annotated[Ctx, Depends(w.ctx)],
        tab: str = "actions",
        who: str = "",
        kind: str = "",
        since: str = "",
        until: str = "",
        w_: Annotated[str, Query(alias="w")] = "",
    ) -> Response:
        if tab == "logins" and c.role != "admin":
            return journal_page(w, c, tab="actions", win="logins_deny")
        win = {"filters": "filters", "export": "export"}.get(w_, "")
        return journal_page(w, c, tab=tab if tab in ("actions", "logins") else "actions", who=who,
                            kind=kind, since=since, until=until, win=win)  # fmt: skip

    @app.get("/journal/{n}", response_class=HTMLResponse)
    def journal_entry(n: str, c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        e = d.journal.get(c.company, n)
        if e is None:
            raise HTTPException(404, "Запись не найдена")
        return journal_page(w, c, win="entry", entry=e)

    @app.post("/journal/export", response_class=HTMLResponse)
    def journal_export(
        c: Annotated[Ctx, Depends(w.ctx)],
        since: Annotated[str, Form()] = "",
        until: Annotated[str, Form()] = "",
        fmt: Annotated[str, Form()] = "csv",
    ) -> Response:
        sd, ud = _date(since), _date(until)
        today = c.today
        sd = sd or today - timedelta(days=30)
        ud = ud or today
        form = {"since": since, "until": until, "fmt": fmt}
        if ud < sd:
            return journal_page(
                w, c, win="export", form=form, errors={"since": "Начало позже конца периода."}
            )
        if (ud - sd).days > 365:
            return journal_page(
                w,
                c,
                win="export_err",
                form=form,
                errors={"since": "Журнал хранится 1 год: период не больше 365 дней."},
            )
        rows = [e for e in d.journal.entries(c.company) if sd <= c.local(e.at).date() <= ud]
        d.journal.add(c.company, c.now, c.user.email, "log.export", f"{sd:%d.%m.%Y}–{ud:%d.%m.%Y}")
        lines = [
            f"{c.local(e.at):%d.%m.%Y %H:%M} · {e.user} · {e.label} · {e.target}" for e in rows
        ]
        if fmt == "pdf":
            data, kind = _rows_pdf("Журнал действий", lines)
            mime = "application/pdf" if kind == "pdf" else "text/html; charset=utf-8"
            return Response(data, media_type=mime,
                            headers={"Content-Disposition": f'attachment; filename="journal.{kind}"'})  # fmt: skip
        buf = io.StringIO()
        wr = csv.writer(buf, delimiter=";")
        wr.writerow(["Когда", "Кто", "Что", "С чем"])
        for e in rows:
            wr.writerow([f"{c.local(e.at):%d.%m.%Y %H:%M}", e.user, e.label, e.target])
        return Response(
            ("\ufeff" + buf.getvalue()).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="journal.csv"'},
        )
