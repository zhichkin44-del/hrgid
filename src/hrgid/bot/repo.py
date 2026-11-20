"""Типизированный слой над DocStore: сущности бота и очереди HR."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from hrgid.bot.models import (
    Announcement,
    CompanySettings,
    Employee,
    Feedback,
    InviteCode,
    KbEntry,
    Out,
    Throttle,
    Ticket,
)
from hrgid.bot.store import DocStore

M = TypeVar("M", bound=BaseModel)


def _dump(m: BaseModel) -> dict[str, Any]:
    out: dict[str, Any] = m.model_dump(mode="json")
    return out


class Repo:
    def __init__(self, store: DocStore) -> None:
        self.store = store

    def _get(self, model: type[M], kind: str, company_id: str, key: str) -> M | None:
        d = self.store.get(kind, company_id, key)
        return None if d is None else model.model_validate(d)

    def _all(self, model: type[M], kind: str, company_id: str) -> list[M]:
        return [model.model_validate(d) for d in self.store.all(kind, company_id)]

    # --- настройки ---------------------------------------------------------
    def settings(self, company_id: str) -> CompanySettings:
        return self._get(CompanySettings, "settings", company_id, "main") or CompanySettings(
            company_id=company_id
        )

    def save_settings(self, s: CompanySettings) -> None:
        self.store.put("settings", s.company_id, "main", _dump(s))

    # --- сотрудники --------------------------------------------------------
    def save_employee(self, e: Employee) -> None:
        self.store.put("employee", e.company_id, e.id, _dump(e))

    def employee(self, company_id: str, employee_id: str) -> Employee | None:
        return self._get(Employee, "employee", company_id, employee_id)

    def employees(self, company_id: str) -> list[Employee]:
        return self._all(Employee, "employee", company_id)

    def employee_by_telegram(self, telegram_id: int) -> Employee | None:
        for _, d in self.store.find("employee", "telegram_id", str(telegram_id)):
            return Employee.model_validate(d)
        return None

    # --- коды --------------------------------------------------------------
    def save_code(self, c: InviteCode) -> None:
        self.store.put("code", c.company_id, c.id, _dump(c))

    def codes_of(self, company_id: str, employee_id: str) -> list[InviteCode]:
        return [
            c for c in self._all(InviteCode, "code", company_id) if c.employee_id == employee_id
        ]

    def code_by_hash(self, code_hash: str) -> InviteCode | None:
        for _, d in self.store.find("code", "code_hash", code_hash):
            return InviteCode.model_validate(d)
        return None

    def throttle(self, telegram_id: int) -> Throttle:
        d = self.store.get("throttle", "", str(telegram_id))
        return Throttle.model_validate(d) if d else Throttle(telegram_id=telegram_id)

    def save_throttle(self, t: Throttle) -> None:
        self.store.put("throttle", "", str(t.telegram_id), _dump(t))

    # --- обращения ---------------------------------------------------------
    def next_ticket_key(self, company_id: str, confidential: bool) -> str:
        if confidential:
            return f"K{self.store.next_seq(company_id, 'conf_ticket', 1)}"
        return str(self.store.next_seq(company_id, "ticket", 101))

    def save_ticket(self, t: Ticket) -> None:
        self.store.put("ticket", t.company_id, t.key, _dump(t))

    def ticket(self, company_id: str, key: str) -> Ticket | None:
        return self._get(Ticket, "ticket", company_id, key)

    def tickets(self, company_id: str) -> list[Ticket]:
        return self._all(Ticket, "ticket", company_id)

    # --- база ответов ------------------------------------------------------
    def next_kb_id(self, company_id: str) -> int:
        return self.store.next_seq(company_id, "kb", 1)

    def save_kb(self, e: KbEntry) -> None:
        self.store.put("kb", e.company_id, f"{e.id:08d}", _dump(e))

    def kb_entries(self, company_id: str) -> list[KbEntry]:
        return self._all(KbEntry, "kb", company_id)

    # --- объявления, оценки ------------------------------------------------
    def save_announcement(self, a: Announcement) -> None:
        self.store.put("announcement", a.company_id, a.id, _dump(a))

    def announcement(self, company_id: str, ann_id: str) -> Announcement | None:
        return self._get(Announcement, "announcement", company_id, ann_id)

    def announcements(self, company_id: str) -> list[Announcement]:
        return self._all(Announcement, "announcement", company_id)

    def save_feedback(self, f: Feedback) -> None:
        self.store.put("feedback", f.company_id, f.id, _dump(f))

    def feedback(self, company_id: str) -> list[Feedback]:
        return self._all(Feedback, "feedback", company_id)

    # --- outbox: сообщения, которые доставляет бот (уведомления HR, ответы HR, объявления) ----
    def enqueue(self, out: Out, now: datetime) -> str:
        n = self.store.next_seq("", "outbox", 1)
        key = f"{n:010d}"
        self.store.put(
            "outbox",
            out.company_id,
            key,
            {"id": key, "status": "pending", "at": now.isoformat(), "out": _dump(out)},
        )
        return key

    def outbox_pending(self) -> list[tuple[str, str, Out]]:
        rows = self.store.find("outbox", "status", "pending")
        res = [(c, str(d["id"]), Out.model_validate(d["out"])) for c, d in rows]
        return sorted(res, key=lambda r: r[1])

    def outbox_mark(self, company_id: str, key: str, status: str) -> None:
        d = self.store.get("outbox", company_id, key)
        if d is not None:
            d["status"] = status
            self.store.put("outbox", company_id, key, d)
