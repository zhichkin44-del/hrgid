"""Запуск панели HR на наполненном стенде (для разработки и снимков экранов).

    python scripts/panel_serve.py --port 8765

Хранилище — в памяти; письма с кодом входа складываются в каталог outbox/ (по файлу на письмо).
Данные — «Студия Жичкина»: сотрудники, обращения, база ответов, правила с проверкой на ТК.
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hrgid.bot.models import CompanySettings, Employee  # noqa: E402
from hrgid.bot.store import MemoryDocStore  # noqa: E402
from hrgid.docs.service import DocService  # noqa: E402
from hrgid.docs.tkcheck import TkChecker  # noqa: E402
from hrgid.panel.app import create_panel_app  # noqa: E402
from hrgid.panel.deps import PanelDeps, build_deps  # noqa: E402
from hrgid.panel.mailer import FileMailer  # noqa: E402

CO = "studio"
RULES = [
    "Правила работы студии",
    "1. Общие положения",
    "1.1. Правила действуют для всех работников, принятых по трудовому договору.",
    "2. Отпуск",
    "2.1. Ежегодный основной оплачиваемый отпуск предоставляется продолжительностью 31 календарный день.",
    "3. Оплата труда",
    "3.1. Заработная плата выплачивается один раз в месяц.",
]


def build(outbox: Path, now: datetime | None = None) -> tuple[PanelDeps, datetime]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "docs"))
    from helpers import KeywordNormSource, make_fake  # type: ignore[import-not-found]

    clock_now = now or datetime.now(UTC)
    store = MemoryDocStore()

    def checker(docs: DocService) -> TkChecker:
        return TkChecker(make_fake(), KeywordNormSource(), docs)

    deps = build_deps(
        store, checker_factory=checker, mailer=FileMailer(outbox), clock=lambda: clock_now
    )
    deps.repo.save_settings(
        CompanySettings(
            company_id=CO, company_name="Студия Жичкина", hr_name="Анна Воронова",
            urgent_contact="Анна, HR: +7 900 000-00-00", code_prefix="ЖС",
        )
    )  # fmt: skip
    deps.auth.add_user(CO, "anna@studio.test", "Анна Воронова", "hr")
    deps.auth.add_user(CO, "maria@studio.test", "Мария Орлова", "admin")
    return deps, clock_now


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--outbox", default="outbox")
    args = ap.parse_args()
    deps, now = build(Path(args.outbox))
    doc = Document()
    for line in RULES:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    deps.docs.upload(CO, "rules.docx", buf.getvalue(), "anna@studio.test", "Правила студии")
    for i, (name, pos, grp) in enumerate(
        [("Ольга Смирнова", "дизайнер", "Дизайн"), ("Игорь Петров", "монтажёр", "Монтаж")]
    ):
        e = Employee(id=f"e{i + 1}", company_id=CO, full_name=name, position=pos, group=grp,
                     telegram_id=5000 + i, consent_at=now)  # fmt: skip
        deps.repo.save_employee(e)
        deps.desk.create(e, "Можно ли перенести отпуск с декабря на январь?", now)
    print(
        f"Панель: http://127.0.0.1:{args.port}  (вход: anna@studio.test; код — в каталоге {args.outbox}/)"
    )
    uvicorn.run(create_panel_app(deps), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
