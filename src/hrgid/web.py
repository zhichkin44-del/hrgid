"""Минимальный веб-чат (§4.10): тот же поток, что в боте; вход по ссылке-приглашению.

Без расчётов профиля и без конфиденциального канала. Оформление — функциональное, без дизайна.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from hrgid.flow.graph import AskRequest, Pipeline


@dataclass(frozen=True)
class Invite:
    company_id: str
    group: str = ""


class ChatIn(BaseModel):
    invite: str
    question: str = Field(min_length=1, max_length=2000)
    history: list[str] = Field(default_factory=list, max_length=20)


class SourceOut(BaseModel):
    fragment_id: int
    ref: str
    quote: str


class ChatOut(BaseModel):
    status: str
    text: str
    route: str
    sources: list[SourceOut]
    cached: bool


PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>HRgid</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<body style="font-family:sans-serif;max-width:640px;margin:1em auto;padding:0 1em">
<h3>HRgid</h3><p><small>Не юридическая консультация. Ответ — со ссылкой на источник.</small></p>
<div id="log"></div>
<form id="f"><input id="q" style="width:80%" autocomplete="off" required>
<button>Спросить</button></form>
<script>
const invite = location.pathname.split('/').pop();
const hist = [];
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const q = document.getElementById('q').value; document.getElementById('q').value = '';
  const log = document.getElementById('log');
  log.insertAdjacentHTML('beforeend', '<p><b></b></p>');
  log.lastChild.firstChild.textContent = q;
  const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({invite, question: q, history: hist.slice(-6)})});
  const d = await r.json();
  hist.push(q);
  const p = document.createElement('p'); p.textContent = d.text || d.detail || 'Ошибка';
  (d.sources || []).forEach(s => {
    const d2 = document.createElement('details');
    d2.innerHTML = '<summary></summary><blockquote></blockquote>';
    d2.firstChild.textContent = 'Источник: ' + s.ref; d2.lastChild.textContent = s.quote;
    p.appendChild(d2);
  });
  log.appendChild(p);
};
</script></body></html>"""


def create_app(pipeline: Pipeline, invites: dict[str, Invite]) -> FastAPI:
    app = FastAPI(title="HRgid", docs_url=None, redoc_url=None)

    @app.get("/c/{token}", response_class=HTMLResponse)
    def page(token: str) -> str:
        if token not in invites:
            raise HTTPException(404, "Ссылка недействительна")
        return PAGE

    @app.post("/api/chat", response_model=ChatOut)
    def chat(body: ChatIn) -> ChatOut:
        inv = invites.get(body.invite)
        if inv is None:
            raise HTTPException(403, "Ссылка недействительна")
        ans = pipeline.ask(
            AskRequest(
                body.question, inv.company_id, inv.group, tuple(body.history[-6:]), profile=None
            )
        )
        return ChatOut(
            status=ans.status,
            text=ans.text,
            route=ans.route,
            sources=[
                SourceOut(fragment_id=s.fragment_id, ref=s.ref, quote=s.quote) for s in ans.sources
            ],
            cached=ans.cached,
        )

    return app
