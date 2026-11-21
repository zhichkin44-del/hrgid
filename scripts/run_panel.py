"""Runs the HR panel for the owner: http://127.0.0.1:8090

    python scripts/run_panel.py

The company "Студия Жичкина" is already populated: rules draft (data/companies/studio/rules-draft.md),
employee roles, 12 requests in the queue, answer base, announcements, Labor Code check. Model - Fake,
no keys needed. Sign-in: email + one-time code; the code is printed in this console.
Logins: admin@studio.test (administrator), hr@studio.test (HR).
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import uvicorn
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "docs"))

from helpers import KeywordNormSource, make_fake  # type: ignore[import-not-found]  # noqa: E402

from hrgid.bot.models import CompanySettings, Employee  # noqa: E402
from hrgid.bot.store import MemoryDocStore  # noqa: E402
from hrgid.docs.service import DocService  # noqa: E402
from hrgid.docs.tkcheck import TkChecker  # noqa: E402
from hrgid.panel.app import create_panel_app  # noqa: E402
from hrgid.panel.deps import PanelDeps, build_deps  # noqa: E402
from hrgid.panel.mailer import ConsoleMailer  # noqa: E402

CO = "studio"
ADMIN, HR = "admin@studio.test", "hr@studio.test"
PEOPLE = [
    ("Смирнова Ольга", "дизайнер", "Дизайн", date(2024, 3, 11)),
    ("Петров Игорь", "монтажёр", "Монтаж", date(2025, 4, 1)),
    ("Козлов Дмитрий", "разработчик", "Разработка", date(2023, 6, 3)),
    ("Лебедева Анастасия", "менеджер проектов", "Менеджеры", date(2022, 8, 15)),
    ("Никитин Сергей", "монтажёр", "Монтаж", date(2021, 5, 17)),
    ("Орлова Мария", "руководитель", "Руководство", date(2020, 1, 20)),
    ("Волков Артём", "разработчик", "Разработка", date(2024, 9, 2)),
    ("Зайцева Елена", "дизайнер", "Дизайн", date(2023, 2, 27)),
    ("Громов Павел", "менеджер проектов", "Менеджеры", date(2025, 8, 11)),
]
QUESTIONS = [
    "Можно ли перенести отпуск с декабря на январь?",
    "Можно ли работать из другого города две недели?",
    "Как заказать справку о доходах для банка?",
    "Как оформить отгул за работу в субботу?",
    "Как оформить командировку в Казань?",
    "Положена ли оплата, если задержалась на два часа?",
    "Можно ли получить аванс раньше?",
    "Сколько дней отпуска на испытательном сроке?",
    "Как оплачивается работа в выходной день?",
    "Что делать, если заболел в отпуске?",
    "Когда выплачивают зарплату в декабре?",
    "Можно ли уйти по соглашению сторон раньше срока?",
]


def rules_docx() -> bytes:
    doc = Document()
    text = (ROOT / "data" / "companies" / "studio" / "rules-draft.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip() and not line.startswith(">"):
            doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build(now: datetime) -> PanelDeps:
    store = MemoryDocStore()

    def checker(docs: DocService) -> TkChecker:
        return TkChecker(make_fake(), KeywordNormSource(), docs)

    deps = build_deps(
        store, checker_factory=checker, mailer=ConsoleMailer(), clock=lambda: datetime.now(UTC)
    )
    deps.repo.save_settings(
        CompanySettings(
            company_id=CO, company_name="Студия Жичкина", hr_name="Анна Воронова",
            urgent_contact="Анна, HR: +7 900 000-00-00", code_prefix="ЖС",
        )
    )  # fmt: skip
    deps.auth.add_user(CO, ADMIN, "Мария Орлова", "admin")
    deps.auth.add_user(CO, HR, "Анна Воронова", "hr")
    deps.auth.add_user(CO, "colleague@studio.test", "Павел Громов", "hr")
    emps = []
    for i, (name, pos, grp, hd) in enumerate(PEOPLE):
        e = Employee(
            id=f"e{i + 1}", company_id=CO, full_name=name, position=pos, group=grp, hire_date=hd,
            schedule="5/2", profile_date=date(2026, 11, 12),
            telegram_id=5000 + i if i < 7 else None, consent_at=now if i < 7 else None,
        )  # fmt: skip
        deps.repo.save_employee(e)
        emps.append(e)
    v = deps.docs.upload(CO, "rules-draft.docx", rules_docx(), ADMIN, "Правила работы студии")
    assert deps.checker is not None
    deps.checker.check_version(CO, v.doc_id, v.number, ADMIN)
    for i, q in enumerate(QUESTIONS):
        t = deps.desk.create(emps[i % 7], q, now - timedelta(minutes=25 * (len(QUESTIONS) - i)))
        if i % 4 == 0:
            deps.desk.assign(CO, t.key, HR)
    for q, a, basis in [
        ("Сколько дней отпуска положено", "28 календарных дней по закону.", "ст. 115 ТК РФ"),
        (
            "Как оформить командировку",
            "Заявление руководителю за 3 дня до выезда.",
            "п. 5.1 Правил",
        ),
        ("Когда выплачивают зарплату", "Два раза в месяц: 25 числа и 10 числа.", "ст. 136 ТК РФ"),
    ]:
        deps.kb.add(CO, question=q, answer=a, basis=basis, author="Анна Воронова", now=now)
    deps.desk.publish_announcement(
        CO, "График отпусков на 2027 год", "Напишите желаемые даты HR до 30 ноября.",
        "Анна Воронова", now - timedelta(days=1),
    )  # fmt: skip
    deps.desk.publish_announcement(
        CO, "Часы работы HR в пятницу", "В пятницу HR на связи с 10:00 до 15:00.",
        "Анна Воронова", now - timedelta(days=3), groups=["Монтаж"],
    )  # fmt: skip
    for q, cost, ms in [
        ("Сколько дней отпуска у [ФИО_1]", 6.4, 900),
        ("Когда выплата зарплаты", 4.1, 1300),
        ("Как оплачивается работа в выходной", 9.8, 2400),
    ]:
        deps.ops.log_question(CO, masked_question=q, cost_rub=cost, latency_ms=ms, now=now)
    return deps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()
    deps = build(datetime.now(UTC))
    print(f"Панель HRgid: http://127.0.0.1:{args.port}")
    print(f"Вход: {ADMIN} (администратор) или {HR} (HR). Код придёт сюда, в консоль.")
    uvicorn.run(create_panel_app(deps), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
