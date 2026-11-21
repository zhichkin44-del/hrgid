"""HR queue (SPEC-09 §4.6-4.7, §8 P2): requests, statuses, assignment, SLA, replies.

Without the web panel (it comes in stage 6): the panel calls the same methods. Everything that must
be delivered to
Telegram (HR notification, reply to the employee, announcement) goes into the outbox; the bot
delivers it.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from hrgid.bot import hours
from hrgid.bot.kb import KbService
from hrgid.bot.models import (
    Announcement,
    Button,
    Employee,
    KbEntry,
    Out,
    Ticket,
    TicketKind,
    TicketMessage,
)
from hrgid.bot.repo import Repo

Tab = Literal["all", "common", "mine", "conf", "overdue"]


class DeskError(ValueError):
    pass


@dataclass(frozen=True)
class KbParams:
    question: str
    answer: str
    basis: str
    audience: str = "all"


@dataclass(frozen=True)
class HrView:
    """HR request card: for a confidential request the name stays hidden until the author reveals
    it."""

    key: str
    label: str
    text: str
    employee_name: str | None
    status: str
    assignee: str
    due_at: datetime
    overdue: bool
    confidential: bool
    delivery_failed: bool


class HrDesk:
    def __init__(self, repo: Repo, kb: KbService) -> None:
        self.repo = repo
        self.kb = kb

    # --- creation ---
    def create(
        self,
        emp: Employee,
        text: str,
        now: datetime,
        *,
        kind: TicketKind = "question",
        confidential: bool = False,
    ) -> Ticket:
        text = text.strip()
        if not text:
            raise DeskError("Пустое обращение")
        s = self.repo.settings(emp.company_id)
        key = self.repo.next_ticket_key(emp.company_id, confidential)
        t = Ticket(
            key=key,
            company_id=emp.company_id,
            employee_id=emp.id,
            kind="confidential" if confidential else kind,
            confidential=confidential,
            text=text[:2000],
            messages=[TicketMessage(author="employee", text=text[:2000], at=now)],
            created_at=now,
            due_at=hours.add_working_hours(now, s.sla_hours, s),
            out_of_hours=not hours.is_open(now, s),
        )
        self.repo.save_ticket(t)
        self._notify_hr(t, emp, now, f"Новое обращение {t.label}")
        return t

    def _notify_hr(self, t: Ticket, emp: Employee | None, now: datetime, head: str) -> None:
        s = self.repo.settings(t.company_id)
        if s.hr_chat_id is None:
            return
        if t.confidential and not t.revealed:
            who = "автор скрыт"
        else:
            who = emp.full_name if emp else "сотрудник"
        label = head.replace(t.label, f"{t.label} (конфиденциально)") if t.confidential else head
        body = t.messages[-1].text
        due = hours.deadline_phrase(now, t.due_at, s)
        self.repo.enqueue(
            Out(
                chat_id=s.hr_chat_id,
                company_id=t.company_id,
                text=f"{label} · {who}\n{body}\nСрок ответа: {due}",
                meta={"ticket": t.key},
            ),
            now,
        )

    # --- employee actions ---
    def append(self, t: Ticket, text: str, now: datetime) -> Ticket:
        text = text.strip()[:2000]
        if not text:
            raise DeskError("Пустое дополнение")
        if t.status != "queue":
            raise DeskError("Обращение уже не в очереди")
        t = t.model_copy(
            update={"messages": [*t.messages, TicketMessage(author="employee", text=text, at=now)]}
        )
        self.repo.save_ticket(t)
        emp = self.repo.employee(t.company_id, t.employee_id)
        self._notify_hr(t, emp, now, f"Дополнение к обращению {t.label}")
        return t

    def withdraw(self, t: Ticket) -> Ticket:
        if t.status != "queue":
            raise DeskError("Отозвать можно только обращение в очереди")
        t = t.model_copy(update={"status": "withdrawn"})
        self.repo.save_ticket(t)
        return t

    def rate(self, t: Ticket, vote: Literal["up", "down"]) -> Ticket:
        if t.status != "answered":
            raise DeskError("Оценить можно только отвеченное обращение")
        t = t.model_copy(update={"rating": vote, "status": "closed"})
        self.repo.save_ticket(t)
        return t

    def reveal(self, t: Ticket) -> Ticket:
        """The employee revealed the HR name; this cannot be undone (Б7.1)."""
        t = t.model_copy(update={"revealed": True})
        self.repo.save_ticket(t)
        return t

    def request_delete(self, t: Ticket, now: datetime) -> Ticket:
        s = self.repo.settings(t.company_id)
        t = t.model_copy(
            update={
                "status": "deleted_pending",
                "delete_deadline": now + timedelta(seconds=s.delete_undo_sec),
            }
        )
        self.repo.save_ticket(t)
        return t

    def undo_delete(self, t: Ticket, now: datetime) -> Ticket | None:
        if t.status != "deleted_pending" or not t.delete_deadline or now > t.delete_deadline:
            return None
        t = t.model_copy(update={"status": "queue", "delete_deadline": None})
        self.repo.save_ticket(t)
        return t

    def finalize_company(self, company_id: str, now: datetime) -> int:
        """Permanently deletes confidential requests whose time window for return has expired."""
        n = 0
        for t in self.repo.tickets(company_id):
            if t.status == "deleted_pending" and t.delete_deadline and now > t.delete_deadline:
                self.repo.save_ticket(
                    t.model_copy(update={"status": "deleted", "text": "", "messages": []})
                )
                n += 1
        return n

    # --- HR actions ---
    def get(self, company_id: str, key: str) -> Ticket:
        t = self.repo.ticket(company_id, key)
        if t is None:
            raise DeskError("Обращение не найдено")
        return t

    def assign(self, company_id: str, key: str, hr_user: str) -> Ticket:
        t = self.get(company_id, key)
        if t.status != "queue":
            raise DeskError("Назначить можно только обращение в очереди")
        t = t.model_copy(update={"assignee": hr_user})
        self.repo.save_ticket(t)
        return t

    def answer(
        self,
        company_id: str,
        key: str,
        hr_user: str,
        text: str,
        now: datetime,
        *,
        save_to_kb: KbParams | None = None,
    ) -> tuple[Ticket, KbEntry | None]:
        """HR reply goes to the employee's chat (via the outbox); optionally, "save to the base"."""
        t = self.get(company_id, key)
        text = text.strip()
        if not text:
            raise DeskError("Текст ответа обязателен")
        if t.status not in ("queue", "answered"):
            raise DeskError("На это обращение ответить нельзя")
        if save_to_kb is not None and t.confidential:
            raise DeskError("Конфиденциальные обращения не попадают в базу ответов")
        entry: KbEntry | None = None
        if save_to_kb is not None:  # field error, checked before the reply is sent
            entry = self.kb.add(
                company_id,
                question=save_to_kb.question,
                answer=save_to_kb.answer,
                basis=save_to_kb.basis,
                audience=save_to_kb.audience,
                author=hr_user,
                now=now,
                source_ticket=t.key,
            )
        s = self.repo.settings(company_id)
        t = t.model_copy(
            update={
                "status": "answered",
                "assignee": t.assignee or hr_user,
                "answered_at": now,
                "saved_to_kb": entry is not None,
                "messages": [
                    *t.messages,
                    TicketMessage(author="hr", text=text, at=now, hr_user=hr_user),
                ],
            }
        )
        emp = self.repo.employee(company_id, t.employee_id)
        if emp is None or emp.telegram_id is None or emp.status != "active":
            t = t.model_copy(update={"delivery_failed": True})
        else:
            who = hr_user or s.hr_name
            self.repo.enqueue(
                Out(
                    chat_id=emp.telegram_id,
                    company_id=company_id,
                    text=(
                        f"Ответ HR · обращение {t.label}\n{who}, HR\n\n{text}\n\n"
                        "Ответ оказался полезным?"
                    ),
                    rows=[
                        [
                            Button(text="👍", data=f"hrr:up:{t.key}"),
                            Button(text="👎", data=f"hrr:down:{t.key}"),
                        ]
                    ],
                    meta={"ticket": t.key},
                ),
                now,
            )
        self.repo.save_ticket(t)
        return t, entry

    def mark_delivery_failed(self, company_id: str, key: str) -> None:
        t = self.repo.ticket(company_id, key)
        if t is not None:
            self.repo.save_ticket(t.model_copy(update={"delivery_failed": True}))

    # --- lists and cards ---
    def is_overdue(self, t: Ticket, now: datetime) -> bool:
        return t.status == "queue" and now > t.due_at

    def list_tickets(
        self, company_id: str, tab: Tab, now: datetime, hr_user: str = ""
    ) -> list[HrView]:
        res: list[Ticket] = []
        for t in self.repo.tickets(company_id):
            if t.status != "queue":
                continue
            if tab == "conf" and not t.confidential:
                continue
            if tab == "common" and (t.confidential or t.assignee):
                continue
            if tab == "mine" and t.assignee != hr_user:
                continue
            if tab == "overdue" and not self.is_overdue(t, now):
                continue
            res.append(t)
        res.sort(key=lambda t: t.due_at)
        return [self.view(t, now) for t in res]

    def view(self, t: Ticket, now: datetime) -> HrView:
        emp = self.repo.employee(t.company_id, t.employee_id)
        hidden = t.confidential and not t.revealed
        return HrView(
            key=t.key,
            label=t.label,
            text=t.text,
            employee_name=None if hidden or emp is None else emp.full_name,
            status=t.status,
            assignee=t.assignee,
            due_at=t.due_at,
            overdue=self.is_overdue(t, now),
            confidential=t.confidential,
            delivery_failed=t.delivery_failed,
        )

    def analytics_rows(self, company_id: str) -> list[dict[str, str]]:
        """Rows for analytics: confidential ones have no text and no author (§4.7)."""
        rows: list[dict[str, str]] = []
        for t in self.repo.tickets(company_id):
            if t.status in ("deleted", "deleted_pending"):
                continue
            rows.append(
                {
                    "key": t.key,
                    "kind": t.kind,
                    "status": t.status,
                    "text": "" if t.confidential else t.text,
                    "created": t.created_at.isoformat(),
                }
            )
        return rows

    def employee_tickets(self, emp: Employee) -> list[Ticket]:
        mine = [
            t
            for t in self.repo.tickets(emp.company_id)
            if t.employee_id == emp.id and t.status not in ("deleted", "deleted_pending")
        ]
        return sorted(mine, key=lambda t: t.created_at, reverse=True)

    # --- employee data (Б10, П6.12) ---
    def purge_employee(self, company_id: str, employee_id: str) -> None:
        """Deletes the employee's personal data: profile, link, request texts; the record of the
        fact remains."""
        emp = self.repo.employee(company_id, employee_id)
        if emp is None:
            return
        for t in self.repo.tickets(company_id):
            if t.employee_id == employee_id:
                self.repo.save_ticket(
                    t.model_copy(update={"text": "", "messages": [], "employee_id": employee_id})
                )
        self.repo.save_employee(
            Employee(
                id=emp.id,
                company_id=company_id,
                full_name="Данные удалены",
                status="disabled",
            )
        )

    # --- announcements (Б9, П11) ---
    def publish_announcement(
        self,
        company_id: str,
        title: str,
        text: str,
        author: str,
        now: datetime,
        *,
        asks_dates: bool = False,
        groups: Sequence[str] = (),
    ) -> Announcement:
        a = Announcement(
            id=secrets.token_hex(4),
            company_id=company_id,
            title=title,
            text=text,
            author=author,
            created_at=now,
            asks_dates=asks_dates,
            groups=list(groups),
        )
        rows = [[Button(text="Прочитано", data=f"ann:read:{a.id}")]]
        if asks_dates:
            rows.append([Button(text="Написать даты HR", data=f"ann:dates:{a.id}")])
        for e in self.repo.employees(company_id):
            if groups and e.group not in groups:
                continue
            if e.status == "active" and e.telegram_id is not None and e.consent_at is not None:
                a.recipients.append(e.id)
                self.repo.enqueue(
                    Out(
                        chat_id=e.telegram_id,
                        company_id=company_id,
                        text=(
                            f"📢 Объявление от HR\n{title}\n\n{text}\n\n"
                            f"{author}, HR · {now:%d.%m.%Y}"
                        ),
                        rows=rows,
                    ),
                    now,
                )
        self.repo.save_announcement(a)
        return a

    def mark_read(self, company_id: str, ann_id: str, employee_id: str, now: datetime) -> bool:
        a = self.repo.announcement(company_id, ann_id)
        if a is None:
            return False
        a.reads.setdefault(employee_id, now)
        self.repo.save_announcement(a)
        return True

    def unread(self, company_id: str, ann_id: str) -> list[str]:
        """Who has not read it (П11.4): full names of active employees."""
        a = self.repo.announcement(company_id, ann_id)
        if a is None:
            return []
        return [
            e.full_name
            for e in self.repo.employees(company_id)
            if e.status == "active"
            and e.telegram_id is not None
            and e.id not in a.reads
            and (not a.recipients or e.id in a.recipients)
        ]
