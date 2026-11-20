"""П7 «Аналитика» и П8 «Расходы и качество»."""

from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from hrgid.panel.ops import MIN_LIMIT, OVER_ACTIONS, percentile
from hrgid.panel.services import K_REQ
from hrgid.panel.web_base import MONTHS_NOM, Ctx, Web


def analytics_page(w: Web, c: Ctx, *, win: str = "", topic: str = "") -> HTMLResponse:
    d = w.d
    has = d.analytics.has_data(c.company)
    return w.page(
        c, "analytics.html", "analytics", has=has, win=win,
        topics=d.analytics.topics(c.company, c.now) if has else [],
        weekly=d.analytics.weekly(c.company, c.now) if has else [],
        unanswered=d.analytics.frequent_unanswered(c.company) if has else [],
        detail=d.analytics.topic_detail(c.company, topic) if topic else {},
        section_title="Аналитика",
    )  # fmt: skip


def costs_page(
    w: Web,
    c: Ctx,
    *,
    win: str = "",
    form: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    status: int = 200,
) -> HTMLResponse:
    d = w.d
    tech = d.ops.tech(c.company)
    b = d.ops.budget(c.company, c.now)
    qs = d.ops.questions(c.company)
    admin = c.role == "admin"
    lat = [float(q["latency_ms"]) for q in qs if q["latency_ms"]]
    perf = {
        "n": len(qs),
        "p50": round(percentile(lat, 50)),
        "p95": round(percentile(lat, 95)),
        "errors": sum(1 for q in qs if q["provider_error"]),
        "cache": sum(1 for q in qs if q["cache_hit"]),
    }
    by_model: dict[str, float] = {}
    for q in qs:
        by_model[q["model"] or "—"] = by_model.get(q["model"] or "—", 0.0) + float(q["cost_rub"])
    recent = list(reversed(qs))[:30] if admin else []
    return w.page(
        c, "costs.html", "costs", status, tech=tech, b=b, perf=perf, by_model=by_model,
        recent=recent, quality=d.quality() if admin else [], win=win, form=form or {},
        errors=errors or {}, over_actions=OVER_ACTIONS, admin=admin, has=bool(qs),
        section_title="Расходы и качество",
        month_name=f"{MONTHS_NOM[c.local(c.now).month - 1]} {c.local(c.now).year}",
    )  # fmt: skip


def register(app: FastAPI, w: Web) -> None:
    d = w.d

    # ===== П7 =====================================================================================
    @app.get("/analytics", response_class=HTMLResponse)
    def analytics(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return analytics_page(w, c)

    @app.get("/analytics/topic", response_class=HTMLResponse)
    def analytics_topic(c: Annotated[Ctx, Depends(w.ctx)], name: str = "") -> Response:
        if not name:
            raise HTTPException(404, "Тема не найдена")
        return analytics_page(w, c, win="topic", topic=name)

    # ===== П8 =====================================================================================
    @app.get("/costs", response_class=HTMLResponse)
    def costs(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return costs_page(w, c)

    @app.get("/costs/limit", response_class=HTMLResponse)
    def limit_form(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        w.require(c, "budget.edit")
        t = d.ops.tech(c.company)
        return costs_page(
            w, c, win="limit", form={"limit": f"{t.budget_rub:.0f}", "action": t.over_action}
        )

    @app.post("/costs/limit", response_class=HTMLResponse)
    def limit_do(
        c: Annotated[Ctx, Depends(w.ctx)],
        limit: Annotated[str, Form()] = "",
        action: Annotated[str, Form()] = "queue",
    ) -> Response:
        w.require(c, "budget.edit")
        form = {"limit": limit, "action": action}
        try:
            v = float(limit.replace(" ", "").replace(",", "."))
        except ValueError:
            return costs_page(
                w, c, win="limit", form=form, errors={"limit": "Сумма — число, например 15000."}
            )
        if v < MIN_LIMIT:
            return costs_page(
                w, c, win="limit", form=form, errors={"limit": "Лимит — не меньше 1 000 ₽."}
            )
        if action not in OVER_ACTIONS:
            return costs_page(w, c, win="limit", form=form, errors={"action": "Выберите действие."})
        t = d.ops.tech(c.company).model_copy(
            update={"budget_rub": v, "over_action": "queue" if action == "queue" else "refuse"}
        )
        d.ops.save_tech(c.company, t)
        d.journal.add(c.company, c.now, c.user.email, "budget.limit", f"{v:.0f} ₽")
        w.flash(c, "Лимит изменён.")
        return w.see_other("/costs")

    @app.get("/costs/ask", response_class=HTMLResponse)
    def ask_form(c: Annotated[Ctx, Depends(w.ctx)]) -> Response:
        return costs_page(w, c, win="ask")

    @app.post("/costs/ask", response_class=HTMLResponse)
    def ask_do(c: Annotated[Ctx, Depends(w.ctx)], text: Annotated[str, Form()] = "") -> Response:
        if not text.strip():
            return costs_page(
                w,
                c,
                win="ask",
                form={"text": text},
                errors={"text": "Напишите, что нужно изменить."},
            )
        n = d.store.next_seq(c.company, "preq")
        d.store.put(K_REQ, c.company, f"{n:08d}", {"n": f"{n:08d}", "from": c.user.name,
                                                    "at": c.now.isoformat(), "text": text.strip()[:500]})  # fmt: skip
        d.journal.add(c.company, c.now, c.user.email, "budget.request", "")
        w.flash(c, "Запрос отправлен администратору.")
        return w.see_other("/costs")
