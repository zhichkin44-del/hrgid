"""Populating the stand: employees, requests, answer base, a document with a check, spending."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from docx import Document

from hrgid.bot.models import Employee

from .conftest import CO, HR, Env, msk


@dataclass
class Seed:
    olga: Employee
    igor: Employee
    t1: Any
    t2: Any
    conf: Any
    kb_id: int
    doc_id: str


def docx_bytes(lines: list[str]) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


RULES = [
    "Правила работы студии",
    "1. Общие положения",
    "1.1. Правила действуют для всех работников, принятых по трудовому договору.",
    "2. Отпуск",
    "2.1. Ежегодный основной оплачиваемый отпуск предоставляется продолжительностью 31 календарный день.",
    "3. Оплата труда",
    "3.1. Заработная плата выплачивается один раз в месяц.",
]


def seed(env: Env, *, with_doc: bool = True) -> Seed:
    d = env.deps
    olga = env.employee(
        name="Ольга Смирнова",
        emp_id="e1",
        group="Дизайн",
        hire_date=date(2024, 3, 11),
        schedule="5/2",
        salary=Decimal(80000),
    )
    igor = env.employee(name="Игорь Петров", emp_id="e2", position="монтажёр", group="Монтаж")
    olga = env.connect(olga, 5001)
    igor = env.connect(igor, 5002)
    t1 = env.ticket(olga, "Можно ли перенести отпуск с декабря на январь?")
    env.clock.advance(minutes=20)
    t2 = env.ticket(igor, "Как оформить отгул за работу в субботу?")
    conf = env.ticket(olga, "Меня притесняет руководитель", confidential=True)
    kb = d.kb.add(
        CO,
        question="Сколько дней отпуска положено",
        answer="28 календарных дней, в студии 31.",
        basis="ст. 115 ТК РФ",
        author="Анна Воронова",
        now=env.clock.now,
    )
    doc_id = ""
    if with_doc:
        v = d.docs.upload(CO, "rules.docx", docx_bytes(RULES), HR, "Правила студии")
        doc_id = v.doc_id
    d.ops.log_question(
        CO,
        masked_question="Сколько дней отпуска у [ФИО_1]",
        cost_rub=12.5,
        latency_ms=900,
        now=msk(2026, 11, 17, 11),
    )
    d.ops.log_question(
        CO, masked_question="Когда выплата зарплаты", cost_rub=8.0, latency_ms=2100, route="rules"
    )
    return Seed(olga, igor, t1, t2, conf, kb.id, doc_id)
