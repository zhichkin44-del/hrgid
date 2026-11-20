"""Снимки ключевых экранов панели HR: 1440 и 375 px.

    python scripts/panel_shots.py --out C:/Studio/hh-transfer/out/hrgid-panel-shots

Поднимает панель на наполненном стенде (как scripts/panel_serve.py), входит в браузере по коду из
письма (каталог outbox), обходит экраны и пишет PNG и index.json (экран, адрес, эталон раскладки).
Браузер: встроенный Chromium Playwright, если он есть, иначе установленный Chrome или Edge.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import uvicorn
from docx import Document
from playwright.sync_api import Browser, Playwright, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from panel_serve import CO, RULES, build  # noqa: E402

from hrgid.bot.models import Employee  # noqa: E402
from hrgid.panel.app import create_panel_app  # noqa: E402

# (имя снимка, адрес, роль, эталон в out/hrgid-layouts без суффикса размера)
SHOTS: list[tuple[str, str, str, str]] = [
    ("p0-vhod", "/login", "anon", "p0-pochta"),
    ("p1-segodnya", "/", "hr", "p1-segodnya"),
    ("p2-ochered", "/queue", "hr", "p2-ochered"),
    ("p3-baza-otvetov", "/kb", "hr", "p3-baza-otvetov"),
    ("m-p3-novyj", "/kb/new", "hr", "m-p3-novyj"),
    ("p4-dokumenty-admin", "/docs", "admin", "p4-dokumenty"),
    ("p4-dokumenty-hr", "/docs", "hr", "p4-hr"),
    ("m-p4-zagruzit", "/docs?w=upload", "hr", "m-p4-zagruzit"),
    ("p5-proverka-tk-admin", "/check", "admin", "p5-proverka-tk"),
    ("p5-proverka-tk-hr", "/check", "hr", "p5-hr"),
    ("p6-sotrudniki", "/staff", "hr", "p6-sotrudniki"),
    ("m-p6-import-1", "/staff?w=import1", "hr", "m-p6-import-1"),
    ("p11-obyavleniya", "/announcements", "hr", "p11-obyavleniya"),
    ("m-p11-novoe", "/announcements/new", "hr", "p11-novoe"),
    ("p7-analitika", "/analytics", "hr", "p7-analitika"),
    ("p8-raskhody-admin", "/costs", "admin", "p8-raskhody"),
    ("p8-raskhody-hr", "/costs", "hr", "p8-hr"),
    ("p9-nastroyki", "/settings", "admin", "p9-nastroyki"),
    ("p9-net-prav-hr", "/settings", "hr", "p9-net-prav"),
    ("p10-zhurnal", "/journal", "hr", "p10-zhurnal"),
]


def docx_bytes(lines: list[str]) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def fill(deps: Any, now: datetime) -> None:
    """Богатый набор данных «Студии Жичкина»."""
    d = deps
    people = [
        ("Ольга Смирнова", "дизайнер", "Дизайн", date(2024, 3, 11)),
        ("Игорь Петров", "монтажёр", "Монтаж", date(2025, 4, 1)),
        ("Дмитрий Козлов", "разработчик", "Разработка", date(2023, 6, 3)),
        ("Анастасия Лебедева", "менеджер проектов", "Менеджеры", date(2022, 8, 15)),
        ("Сергей Никитин", "монтажёр", "Монтаж", date(2021, 5, 17)),
    ]
    emps = []
    for i, (name, pos, grp, hd) in enumerate(people):
        e = Employee(
            id=f"e{i + 1}", company_id=CO, full_name=name, position=pos, group=grp, hire_date=hd,
            schedule="5/2", salary=Decimal(90000 + i * 10000), profile_date=date(2026, 11, 12),
            telegram_id=5000 + i if i < 4 else None, consent_at=now if i < 4 else None,
        )  # fmt: skip
        d.repo.save_employee(e)
        emps.append(e)
    d.staff.invite(CO, "Новикова Елена", "16.11.2026", now)
    d.docs.clock = lambda: now
    v = d.docs.upload(CO, "rules.docx", docx_bytes(RULES), "anna@studio.test", "Правила студии")
    d.docs.set_order(CO, v.doc_id, 1, "12", date(2026, 10, 30), date(2026, 11, 1), "maria@studio.test")
    d.checker.check_version(CO, v.doc_id, 1, "anna@studio.test")
    questions = [
        "Можно ли перенести отпуск с декабря на январь?",
        "Можно ли работать из другого города две недели?",
        "Как заказать справку о доходах для банка?",
        "Как оформить отгул за работу в субботу?",
    ]
    tickets = [d.desk.create(emps[i % 4], q, now) for i, q in enumerate(questions)]
    d.desk.create(emps[0], "Меня притесняет руководитель", now, confidential=True)
    d.desk.assign(CO, tickets[0].key, "anna@studio.test")
    d.kb.add(CO, question="Сколько дней отпуска положено", answer="28 календарных дней по закону, в студии — 31.",
             basis="ст. 115 ТК РФ; п. 2.1 Правил", author="Анна Воронова", now=now)  # fmt: skip
    d.kb.add(CO, question="Как оформить командировку", answer="Заявление руководителю за 3 дня до выезда.",
             basis="п. 5.1 Правил", author="Анна Воронова", now=now)  # fmt: skip
    for q, cost, ms in [
        ("Сколько дней отпуска у [ФИО_1]", 6.4, 900),
        ("Когда выплата зарплаты", 4.1, 1300),
        ("Как оплачивается работа в выходной", 9.8, 2400),
        ("Можно ли уйти на больничный по скайпу", 5.2, 1800),
    ]:
        d.ops.log_question(CO, masked_question=q, cost_rub=cost, latency_ms=ms, now=now)
    d.desk.publish_announcement(CO, "График отпусков на 2027 год", "Напишите желаемые даты HR до 30 ноября.",
                                "Анна Воронова", now)  # fmt: skip
    d.journal.add(CO, now, "anna@studio.test", "kb.add", "#1")
    d.journal.add(CO, now, "maria@studio.test", "doc.order", "d1 v1: № 12")


def last_code(outbox: Path, email: str) -> str:
    for f in sorted(outbox.glob("*.txt"), reverse=True):
        t = f.read_text(encoding="utf-8")
        if f"Кому: {email}" in t:
            return re.search(r"\b(\d{6})\b", t).group(1)  # type: ignore[union-attr]
    raise LookupError(email)


def launch(p: Playwright) -> Browser:
    last: Exception | None = None
    for kw in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return p.chromium.launch(headless=True, **kw)  # type: ignore[arg-type]
        except Exception as e:
            last = e
    raise RuntimeError(f"нет браузера для Playwright: {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "shots"))
    ap.add_argument("--port", type=int, default=8791)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    outbox = Path(tempfile.mkdtemp(prefix="hrgid-outbox-"))
    now = datetime(2026, 11, 18, 12, 40, tzinfo=UTC)  # среда, 15:40 мск
    deps, _ = build(outbox, now)
    fill(deps, now)
    server = uvicorn.Server(
        uvicorn.Config(create_panel_app(deps), host="127.0.0.1", port=args.port, log_level="error")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    url = f"http://127.0.0.1:{args.port}"
    index: list[dict[str, str]] = []
    with sync_playwright() as p:
        browser = launch(p)
        for size, (w, h) in {"1440": (1440, 900), "375": (375, 812)}.items():
            ctxs: dict[str, Any] = {}
            for role, email in (("hr", "anna@studio.test"), ("admin", "maria@studio.test"), ("anon", "")):
                ctx = browser.new_context(viewport={"width": w, "height": h}, locale="ru-RU")
                page = ctx.new_page()
                if email:
                    page.goto(url + "/login")
                    page.fill("input[name=email]", email)
                    page.click("button[type=submit]")
                    page.fill("input[name=code]", last_code(outbox, email))
                    page.click("button[type=submit]")
                    page.wait_for_url(url + "/")
                ctxs[role] = page
            for name, path, role, ref in SHOTS:
                page = ctxs[role]
                page.goto(url + path)
                page.wait_for_load_state("networkidle")
                file = out / f"{name}-{size}.png"
                page.screenshot(path=str(file), full_page=True)
                index.append({"name": name, "size": size, "url": path, "role": role,
                              "file": file.name, "reference": f"{ref}-{size}.png"})  # fmt: skip
            for pg in ctxs.values():
                pg.context.close()
        browser.close()
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    server.should_exit = True
    shutil.rmtree(outbox, ignore_errors=True)
    print(f"снимков: {len(index)} -> {out}")


if __name__ == "__main__":
    main()
