"""Модели данных бота и очереди HR (SPEC-09 §4, §9). Все сущности несут company_id (tenant)."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

TicketKind = Literal[
    "question", "confidential", "delete_request", "new_code", "dates", "wrong_login", "failure"
]
TicketStatus = Literal["queue", "answered", "closed", "withdrawn", "deleted_pending", "deleted"]
EmployeeStatus = Literal["active", "disabled"]


class Button(BaseModel):
    text: str
    data: str


class Out(BaseModel):
    """Исходящее сообщение. Доставляет адаптер Telegram; для HR и пушей лежит в outbox."""

    chat_id: int
    text: str
    rows: list[list[Button]] = Field(default_factory=list)
    menu: bool = False  # приложить клавиатуру меню (5 кнопок)
    edit: bool = False  # заменить сообщение, из которого пришла кнопка
    company_id: str = ""
    meta: dict[str, str] = Field(default_factory=dict)  # ticket=<key> и т.п. для отметки сбоя


class CompanySettings(BaseModel):
    """Настройки компании (раздел П9; здесь — значения по умолчанию из SPEC-09 §4.6)."""

    company_id: str
    company_name: str = "Компания"
    hr_name: str = "HR"
    hr_chat_id: int | None = None  # отдельный чат HR для уведомлений
    hours_start: time = time(10, 0)
    hours_end: time = time(19, 0)
    utc_offset: int = 3  # часы; «мск»
    tz_label: str = "мск"
    work_weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    sla_hours: int = 4  # рабочих часов
    urgent_contact: str = ""
    code_prefix: str = "HR"
    dialog_timeout_min: int = 15
    delete_undo_sec: int = 10


class Employee(BaseModel):
    id: str
    company_id: str
    full_name: str
    position: str = ""
    group: str = ""
    status: EmployeeStatus = "active"
    telegram_id: int | None = None
    consent_at: datetime | None = None
    disclaimer_date: date | None = None  # когда показывали «не юридическая консультация»
    # профиль из импорта (§4.4)
    hire_date: date | None = None
    schedule: str = ""
    salary: Decimal | None = None
    profile_date: date | None = None
    earnings_12m: Decimal | None = None
    unused_vacation_days: Decimal | None = None
    used_vacation_days: Decimal = Decimal(0)
    insurance_years: Decimal | None = None
    overtime_ytd: Decimal = Decimal(0)  # сверхурочные часы с начала года (лимит 120 ч, ст. 152)

    @property
    def first_name(self) -> str:
        parts = self.full_name.split()
        if len(parts) >= 3:  # «Фамилия Имя Отчество» из выгрузки
            return parts[1]
        return parts[0] if parts else ""


class InviteCode(BaseModel):
    id: str
    company_id: str
    employee_id: str
    code_hash: str
    status: Literal["active", "used", "revoked"] = "active"
    created_at: datetime
    used_at: datetime | None = None


class Throttle(BaseModel):
    telegram_id: int
    fails: int = 0
    blocked_until: datetime | None = None


class TicketMessage(BaseModel):
    author: Literal["employee", "hr"]
    text: str
    at: datetime
    hr_user: str = ""


class Ticket(BaseModel):
    key: str  # «131» или «K4» (конфиденциальные — своя нумерация)
    company_id: str
    employee_id: str
    kind: TicketKind = "question"
    confidential: bool = False
    status: TicketStatus = "queue"
    text: str
    messages: list[TicketMessage] = Field(default_factory=list)
    assignee: str = ""
    created_at: datetime
    due_at: datetime
    answered_at: datetime | None = None
    rating: Literal["up", "down"] | None = None
    revealed: bool = False
    delete_deadline: datetime | None = None
    saved_to_kb: bool = False
    delivery_failed: bool = False
    out_of_hours: bool = False

    @property
    def label(self) -> str:
        return f"К-{self.key[1:]}" if self.confidential else f"№{self.key}"


class KbEntry(BaseModel):
    id: int
    company_id: str
    question: str
    answer: str
    basis: str  # статья ТК или пункт правил (обязательно)
    audience: str = "all"  # all или название группы
    author: str = ""
    created_at: datetime
    source_ticket: str = ""
    archived: bool = False  # П3.3: в архиве бот не берёт ответ
    needs_review: bool = False  # «проверить»: изменился закон или правило (сценарий 4)
    review_note: str = ""
    uses: int = 0  # сколько раз ответ отдан из базы
    history: list[str] = Field(default_factory=list)  # история изменений (П3.2)


class Announcement(BaseModel):
    id: str
    company_id: str
    title: str
    text: str
    author: str = ""
    created_at: datetime
    asks_dates: bool = False
    reads: dict[str, datetime] = Field(default_factory=dict)  # employee_id -> когда прочитал
    groups: list[str] = Field(default_factory=list)  # пусто = всем
    recipients: list[str] = Field(default_factory=list)  # кому ушло (employee_id)
    reminded_at: datetime | None = None


class Feedback(BaseModel):
    id: str
    company_id: str
    employee_id: str
    vote: Literal["up", "down"]
    route: str = ""
    at: datetime
