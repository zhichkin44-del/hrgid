"""Прикладные операции панели поверх сервисов этапов 2–5 (очередь, база, документы, сотрудники).

Здесь нет HTTP: маршруты только вызывают эти методы. Всё внутри одной компании (company_id берётся
из сессии пользователя, а не из запроса), чужие данные недоступны по построению.
"""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from hrgid.bot import hours
from hrgid.bot.auth import AuthService
from hrgid.bot.desk import HrDesk
from hrgid.bot.kb import SAME_QUESTION, KbService, similarity
from hrgid.bot.models import Button, Employee, KbEntry, Out, Ticket
from hrgid.bot.repo import Repo
from hrgid.bot.store import DocStore
from hrgid.panel import imports
from hrgid.panel.journal import Journal
from hrgid.panel.ops import OpsStore, topic_of
from hrgid.panel.security import PanelAuth

K_GROUP, K_IMPORT, K_UNDO, K_EXPORT, K_DOWN, K_REQ, K_NOTE = (
    "pgroup",
    "pimport",
    "pundo",
    "pexport",
    "pdown",
    "preq",
    "pnote",
)

# Все виды документов компании: нужны выгрузке и удалению данных компании (П9.2, П9.3).
COMPANY_KINDS = (
    "settings", "employee", "code", "ticket", "kb", "announcement", "feedback", "outbox",
    "doc", "docver", "docaudit", "tkcheck", "puser", "plogin", "psess", "plog", "paudit",
    "ptech", "qlog", K_GROUP, K_IMPORT, K_UNDO, K_EXPORT, K_DOWN, K_REQ, K_NOTE,
)  # fmt: skip


class PanelError(ValueError):
    """Ошибка поля формы: field — какое поле подсветить."""

    def __init__(self, field: str, message: str, **extra: str) -> None:
        super().__init__(message)
        self.field = field
        self.extra = extra


def parse_ru_date(s: str) -> date | None:
    return imports.parse_date(s) if s.strip() else None


@dataclass(frozen=True)
class ImportResult:
    created: int
    updated: int
    skipped: int
    disabled: int
    codes: dict[str, str]  # employee_id -> код (только что выданные новым)
    on_date: date


class Undo:
    """«Отменить — 10 с»: действие, которое можно вернуть сразу, или отложенное до конца окна."""

    WINDOW = timedelta(seconds=10)

    def __init__(self, store: DocStore) -> None:
        self.store = store
        self.restore: dict[str, Callable[[str, dict[str, Any]], None]] = {}
        self.finish: dict[str, Callable[[str, dict[str, Any]], None]] = {}

    def add(
        self, company: str, now: datetime, op: str, payload: dict[str, Any], *, deferred: bool
    ) -> str:
        uid = secrets.token_hex(6)
        self.store.put(
            K_UNDO, company, uid,
            {"id": uid, "op": op, "payload": payload, "deferred": deferred,
             "until": (now + self.WINDOW).isoformat()},
        )  # fmt: skip
        return uid

    def cancel(self, company: str, uid: str, now: datetime) -> bool:
        d = self.store.get(K_UNDO, company, uid)
        if d is None or datetime.fromisoformat(d["until"]) < now:
            return False
        if not d["deferred"]:
            self.restore[d["op"]](company, d["payload"])
        self.store.delete(K_UNDO, company, uid)
        return True

    def flush(self, company: str, now: datetime) -> int:
        """Довести до конца отложенные действия, у которых окно отмены закончилось."""
        done = 0
        for d in self.store.all(K_UNDO, company):
            if datetime.fromisoformat(d["until"]) >= now:
                continue
            if d["deferred"]:
                self.finish[d["op"]](company, d["payload"])
                done += 1
            self.store.delete(K_UNDO, company, d["id"])
        return done


class StaffService:
    def __init__(self, repo: Repo, bot_auth: AuthService, desk: HrDesk, store: DocStore) -> None:
        self.repo, self.bot_auth, self.desk, self.store = repo, bot_auth, desk, store

    # --- группы -------------------------------------------------------------------
    def groups(self, company: str) -> list[str]:
        names = {d["name"] for d in self.store.all(K_GROUP, company)}
        names |= {e.group for e in self.repo.employees(company) if e.group}
        return sorted(names)

    def group_sizes(self, company: str) -> dict[str, int]:
        sizes = {g: 0 for g in self.groups(company)}
        for e in self.repo.employees(company):
            if e.group and e.status == "active":
                sizes[e.group] = sizes.get(e.group, 0) + 1
        return sizes

    def add_group(self, company: str, name: str) -> str:
        name = " ".join(name.split())
        if not name:
            raise PanelError("name", "Напишите название группы.")
        if name.lower() in {g.lower() for g in self.groups(company)}:
            raise PanelError("name", "Такая группа уже есть.")
        self.store.put(K_GROUP, company, name, {"name": name})
        return name

    def delete_group(self, company: str, name: str) -> None:
        if any(e.group == name for e in self.repo.employees(company)):
            raise PanelError("name", "Удалить можно только пустую группу.")
        self.store.delete(K_GROUP, company, name)

    # --- сотрудники ---------------------------------------------------------------
    def find_duplicate(self, company: str, full_name: str) -> Employee | None:
        key = imports.norm_name(full_name)
        for e in self.repo.employees(company):
            if imports.norm_name(e.full_name) == key and e.status != "disabled":
                return e
        return None

    def _new_id(self) -> str:
        return "e" + secrets.token_hex(4)

    def invite(
        self,
        company: str,
        full_name: str,
        hire_date: str,
        now: datetime,
        *,
        position: str = "",
        group: str = "",
        schedule: str = "",
    ) -> tuple[Employee, str]:
        full_name = " ".join(full_name.split())
        if not full_name:
            raise PanelError("full_name", "Напишите ФИО сотрудника.")
        hd = parse_ru_date(hire_date)
        if hd is None:
            raise PanelError("hire_date", "Укажите дату приёма, например 31.12.2025.")
        dup = self.find_duplicate(company, full_name)
        if dup is not None:
            raise PanelError("full_name", "Такой сотрудник уже есть.", dup_id=dup.id)
        e = Employee(
            id=self._new_id(), company_id=company, full_name=full_name, position=position.strip(),
            group=group.strip(), hire_date=hd, schedule=schedule.strip(),
        )  # fmt: skip
        self.repo.save_employee(e)
        return e, self.bot_auth.issue_code(company, e.id, now)

    def issue_code(self, company: str, emp_id: str, now: datetime) -> str:
        e = self.get(company, emp_id)
        if e.status != "active":
            raise PanelError("employee", "Сотруднику с отключённым доступом код не выдаётся.")
        return self.bot_auth.issue_code(company, emp_id, now)

    def issue_codes(self, company: str, who: str, now: datetime) -> list[tuple[Employee, str]]:
        """Массовая выдача: «new» — тем, у кого нет Telegram и нет действующего кода; «all» — всем
        без Telegram (старые коды отзываются)."""
        res: list[tuple[Employee, str]] = []
        for e in self.repo.employees(company):
            if e.status != "active" or e.telegram_id is not None:
                continue
            if who == "new" and any(
                c.status == "active" for c in self.repo.codes_of(company, e.id)
            ):
                continue
            res.append((e, self.bot_auth.issue_code(company, e.id, now)))
        return res

    def get(self, company: str, emp_id: str) -> Employee:
        e = self.repo.employee(company, emp_id)
        if e is None:
            raise PanelError("employee", "Сотрудник не найден.")
        return e

    def disable(self, company: str, emp_id: str, *, no_longer_works: bool) -> Employee:
        if not no_longer_works:
            raise PanelError("confirm", "Отметьте, что сотрудник больше не работает.")
        e = self.get(company, emp_id)
        self.bot_auth.revoke_codes(company, emp_id)
        e = e.model_copy(update={"status": "disabled"})
        self.repo.save_employee(e)
        return e

    def enable(self, company: str, emp_id: str) -> Employee:
        e = self.get(company, emp_id).model_copy(update={"status": "active"})
        self.repo.save_employee(e)
        return e

    def edit(
        self,
        company: str,
        emp_id: str,
        *,
        hire_date: str,
        schedule: str,
        salary: str,
        group: str,
        position: str,
        today: date,
    ) -> Employee:
        e = self.get(company, emp_id)
        hd = parse_ru_date(hire_date)
        if hd is None:
            raise PanelError("hire_date", "Укажите дату приёма, например 31.12.2025.")
        sal: Decimal | None = None
        if salary.strip():
            sal = imports.parse_money(salary)
            if sal is None:
                raise PanelError("salary", "Оклад — число не меньше нуля.")
        e = e.model_copy(
            update={
                "hire_date": hd, "schedule": schedule.strip(), "salary": sal,
                "group": group.strip(), "position": position.strip(), "profile_date": today,
            }
        )  # fmt: skip
        self.repo.save_employee(e)
        return e

    def ticket_count(self, company: str, emp_id: str) -> int:
        """Обращения к HR в профиле: конфиденциальные не считаются и не показываются."""
        return sum(
            1
            for t in self.repo.tickets(company)
            if t.employee_id == emp_id
            and not t.confidential
            and t.status not in ("deleted", "deleted_pending")
        )

    # --- импорт 1С ---------------------------------------------------------------------
    def import_start(self, company: str, filename: str, data: bytes) -> str:
        headers, rows = imports.read_table(filename, data)
        mapping = imports.auto_map(headers)
        if "full_name" not in mapping:
            raise imports.ImportError_("no_name", "В файле нет колонки «ФИО».")
        token = secrets.token_hex(8)
        self.store.put(
            K_IMPORT, company, token,
            {"token": token, "headers": headers, "rows": rows, "mapping": mapping,
             "filename": filename},
        )  # fmt: skip
        return token

    def import_doc(self, company: str, token: str) -> dict[str, Any]:
        d = self.store.get(K_IMPORT, company, token)
        if d is None:
            raise PanelError("token", "Импорт не найден: загрузите файл заново.")
        return d

    def import_set_mapping(self, company: str, token: str, mapping: dict[str, int]) -> None:
        d = self.import_doc(company, token)
        for f in imports.REQUIRED:
            if f not in mapping:
                raise PanelError(f, f"Выберите колонку: {imports.FIELDS[f]}.")
        d["mapping"] = mapping
        self.store.put(K_IMPORT, company, token, d)

    def import_plan(self, company: str, token: str) -> imports.Plan:
        d = self.import_doc(company, token)
        mapping = {k: int(v) for k, v in d["mapping"].items()}
        return imports.build_plan(d["rows"], mapping, self.repo.employees(company))

    def import_apply(
        self, company: str, token: str, disable_ids: Sequence[str], now: datetime, today: date
    ) -> ImportResult:
        plan = self.import_plan(company, token)
        codes: dict[str, str] = {}
        for p in plan.new:
            e = Employee(
                id=self._new_id(), company_id=company, full_name=p.full_name, position=p.position,
                group=p.group, hire_date=p.hire_date, schedule=p.schedule, salary=p.salary,
                profile_date=today, earnings_12m=p.earnings_12m,
            )  # fmt: skip
            self.repo.save_employee(e)
        for ex, p in plan.update:
            upd: dict[str, Any] = {"hire_date": p.hire_date, "profile_date": today}
            for k, v in (("position", p.position), ("schedule", p.schedule), ("group", p.group)):
                if v:
                    upd[k] = v
            if p.salary is not None:
                upd["salary"] = p.salary
            if p.earnings_12m is not None:
                upd["earnings_12m"] = p.earnings_12m
            if ex.status == "disabled":
                upd["status"] = "active"  # снова в выгрузке — значит работает
            self.repo.save_employee(ex.model_copy(update=upd))
        missing = {e.id for e in plan.missing}
        n_dis = 0
        for eid in disable_ids:
            if eid in missing:
                self.disable(company, eid, no_longer_works=True)
                n_dis += 1
        self.store.delete(K_IMPORT, company, token)
        return ImportResult(len(plan.new), len(plan.update), len(plan.errors), n_dis, codes, today)

    # --- листы кодов ----------------------------------------------------------------------
    @staticmethod
    def codes_csv(rows: list[tuple[Employee, str]]) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["ФИО", "Группа", "Код приглашения"])
        for e, code in rows:
            w.writerow([e.full_name, e.group, code])
        return "﻿" + buf.getvalue()

    @staticmethod
    def qr_svg(text: str) -> str:
        import segno

        out = io.BytesIO()
        segno.make(text, error="m").save(out, kind="svg", scale=4, border=1, xmldecl=False)
        return out.getvalue().decode("utf-8")


class KbAdmin:
    """База ответов (П3): правка, архив, пересмотр после изменения закона или правил."""

    Q_MAX, A_MAX = 300, 900

    def __init__(self, repo: Repo, kb: KbService) -> None:
        self.repo, self.kb = repo, kb

    def entries(self, company: str, *, archived: bool = False, query: str = "") -> list[KbEntry]:
        rows = [e for e in self.repo.kb_entries(company) if e.archived == archived]
        if query.strip():
            q = query.strip().lower()
            rows = [e for e in rows if q in e.question.lower() or q in e.answer.lower()
                    or similarity(query, e.question) >= 0.5]  # fmt: skip
        return sorted(rows, key=lambda e: e.id, reverse=True)

    def get(self, company: str, entry_id: int) -> KbEntry:
        for e in self.repo.kb_entries(company):
            if e.id == entry_id:
                return e
        raise PanelError("entry", "Ответ не найден.")

    def _check(self, question: str, answer: str, basis: str) -> None:
        if not question.strip():
            raise PanelError("question", "Напишите вопрос в общем виде.")
        if len(question.strip()) > self.Q_MAX:
            raise PanelError("question", f"Вопрос — не длиннее {self.Q_MAX} знаков.")
        if not answer.strip():
            raise PanelError("answer", "Напишите ответ — без текста сохранить нельзя.")
        if len(answer.strip()) > self.A_MAX:
            raise PanelError("answer", f"Ответ — не длиннее {self.A_MAX} знаков.")
        if not basis.strip():
            raise PanelError("basis", "Укажите статью или пункт правил.")

    def add(
        self, company: str, user: str, question: str, answer: str, basis: str, audience: str,
        now: datetime,
    ) -> KbEntry:  # fmt: skip
        self._check(question, answer, basis)
        return self.kb.add(
            company, question=question, answer=answer, basis=basis, author=user, now=now,
            audience=audience or "all",
        )  # fmt: skip

    def edit(
        self, company: str, entry_id: int, user: str, question: str, answer: str, basis: str,
        audience: str, now: datetime,
    ) -> KbEntry:  # fmt: skip
        self._check(question, answer, basis)
        e = self.get(company, entry_id)
        note = f"{now:%d.%m.%Y %H:%M} · {user} исправил(а) ответ"
        e = e.model_copy(
            update={
                "question": question.strip(), "answer": answer.strip(), "basis": basis.strip(),
                "audience": audience or "all", "needs_review": False, "review_note": "",
                "history": [*e.history, note],
            }
        )  # fmt: skip
        self.repo.save_kb(e)
        return e

    def set_archived(self, company: str, entry_id: int, archived: bool) -> KbEntry:
        e = self.get(company, entry_id).model_copy(update={"archived": archived})
        self.repo.save_kb(e)
        return e

    # --- затронутые ответы (сценарий 4) ------------------------------------------------
    @staticmethod
    def _mentions(basis: str, article: str) -> bool:
        a = re.escape(article.strip().lower().replace("ст.", "").strip())
        return bool(re.search(rf"(ст\.?|статья|статьи)\s*{a}(?!\d)", basis.lower()))

    def affected_by_article(self, company: str, article: str) -> list[KbEntry]:
        if not article.strip():
            return []
        return [
            e for e in self.repo.kb_entries(company)
            if not e.archived and self._mentions(e.basis, article)
        ]  # fmt: skip

    def mark_law_change(self, company: str, article: str, now: datetime) -> int:
        """Статья изменилась: ответы, которые на неё опираются, помечаются «проверить»."""
        hit = self.affected_by_article(company, article)
        for e in hit:
            self.repo.save_kb(
                e.model_copy(
                    update={
                        "needs_review": True,
                        "review_note": f"Изменилась статья {article.strip()} ТК РФ ({now:%d.%m.%Y})",
                    }
                )
            )
        return len(hit)

    def mark_by_doc(self, company: str, doc_title: str, note: str) -> int:
        """Изменился документ компании: ответы с основанием «пункт правил» получают пометку."""
        words = [w for w in re.findall(r"[а-яё]{4,}", doc_title.lower())][:2]
        n = 0
        for e in self.repo.kb_entries(company):
            low = e.basis.lower()
            if e.archived or "ст." in low and "п." not in low:
                continue
            if "п." in low or any(w[:5] in low for w in words):
                self.repo.save_kb(e.model_copy(update={"needs_review": True, "review_note": note}))
                n += 1
        return n

    def needs_review(self, company: str) -> list[KbEntry]:
        return sorted(
            (e for e in self.repo.kb_entries(company) if e.needs_review and not e.archived),
            key=lambda e: e.id,
        )

    def decide(self, company: str, entry_id: int, decision: str, now: datetime, user: str) -> None:
        e = self.get(company, entry_id)
        if decision == "keep":
            self.repo.save_kb(
                e.model_copy(
                    update={
                        "needs_review": False, "review_note": "",
                        "history": [*e.history, f"{now:%d.%m.%Y %H:%M} · {user} подтвердил(а) ответ"],
                    }
                )
            )  # fmt: skip
        elif decision == "archive":
            self.repo.save_kb(e.model_copy(update={"needs_review": False, "archived": True}))
        else:
            raise PanelError("decision", "Выберите: оставить, исправить или в архив.")


class Ratings:
    """Разбор оценок 👎 (П1.1): оценка считается разобранной после решения HR."""

    def __init__(self, repo: Repo, store: DocStore) -> None:
        self.repo, self.store = repo, store

    def _resolved(self, company: str) -> set[str]:
        return {d["key"] for d in self.store.all(K_DOWN, company)}

    def open_items(self, company: str, now: datetime) -> list[Ticket]:
        """Обращения с 👎 за последние сутки, ещё не разобранные (конфиденциальные не берём)."""
        done = self._resolved(company)
        since = now - timedelta(days=1)
        return [
            t
            for t in self.repo.tickets(company)
            if t.rating == "down"
            and not t.confidential
            and t.key not in done
            and (t.answered_at or t.created_at) >= since
        ]

    def get(self, company: str, key: str) -> Ticket:
        t = self.repo.ticket(company, key)
        if t is None or t.confidential:
            raise PanelError("rating", "Оценка не найдена.")
        return t

    def resolve(self, company: str, key: str, now: datetime, user: str) -> None:
        self.get(company, key)
        self.store.put(K_DOWN, company, key, {"key": key, "at": now.isoformat(), "by": user})


class CompanyAdmin:
    """Выгрузка и удаление данных компании (П9.2, П9.3)."""

    def __init__(self, store: DocStore, repo: Repo, journal: Journal, auth: PanelAuth) -> None:
        self.store, self.repo, self.journal, self.auth = store, repo, journal, auth

    def export_zip(self, company: str, since: date | None = None) -> bytes:
        """Профили, переписка и обращения (кроме конфиденциальных), база ответов, версии
        документов, журнал. Конфиденциальные обращения не входят."""
        ok_t = [
            t.model_dump(mode="json")
            for t in self.repo.tickets(company)
            if not t.confidential and (since is None or t.created_at.date() >= since)
        ]
        parts: dict[str, Any] = {
            "employees.json": [e.model_dump(mode="json") for e in self.repo.employees(company)],
            "tickets.json": ok_t,
            "kb.json": [e.model_dump(mode="json") for e in self.repo.kb_entries(company)],
            "document_versions.json": self.store.all("docver", company),
            "journal.json": self.store.all("paudit", company),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in parts.items():
                z.writestr(name, json.dumps(data, ensure_ascii=False, indent=1, default=str))
        return buf.getvalue()

    def stash_export(self, company: str, data: bytes, now: datetime) -> str:
        import base64

        token = secrets.token_urlsafe(16)
        self.store.put(
            K_EXPORT, company, token,
            {"token": token, "until": (now + timedelta(hours=24)).isoformat(),
             "data": base64.b64encode(data).decode("ascii")},
        )  # fmt: skip
        return token

    def fetch_export(self, company: str, token: str, now: datetime) -> bytes | None:
        import base64

        d = self.store.get(K_EXPORT, company, token)
        if d is None or datetime.fromisoformat(d["until"]) < now:
            return None
        return base64.b64decode(d["data"])

    def delete_company(self, company: str, typed_name: str, exported: bool) -> None:
        if not exported:
            raise PanelError("exported", "Сначала выгрузите данные компании.")
        real = self.repo.settings(company).company_name
        if typed_name.strip() != real:
            raise PanelError("name", "Название не совпало.")
        self.auth.drop_company_sessions(company)
        for kind in COMPANY_KINDS:
            for d in self.store.all(kind, company):
                for key in _keys_of(self.store, kind, company, d):
                    self.store.delete(kind, company, key)


def _keys_of(store: DocStore, kind: str, company: str, d: dict[str, Any]) -> list[str]:
    """Ключи документа (DocStore отдаёт только данные): подбираем по полям, как их кладут сервисы."""
    cand: list[str] = []
    for f in ("id", "key", "email", "sid", "token", "n", "name", "doc_id", "code_hash"):
        if f in d:
            cand.append(str(d[f]))
    if kind == "kb" and "id" in d:
        cand.append(f"{int(d['id']):08d}")
    if kind == "docver":
        cand.append(f"{d['doc_id']}:{d['number']}")
    if kind == "tkcheck":
        cand.append(f"{d['doc_id']}:{d['version']}")
    if kind == "settings":
        cand.append("main")
    if kind == "ptech":
        cand.append("main")
    return [k for k in dict.fromkeys(cand) if store.get(kind, company, k) is not None]


# --- аналитика (П7) -----------------------------------------------------------------------


@dataclass(frozen=True)
class TopicRow:
    topic: str
    total: int
    this_week: int
    prev_week: int
    without_hr: int
    up: int
    down: int
    weeks: tuple[int, ...] = ()  # 6 недель, от старой к новой (мини-график)

    @property
    def growth(self) -> int:
        return self.this_week - self.prev_week

    @property
    def growth_pct(self) -> int:
        return (
            round(100 * (self.this_week - self.prev_week) / self.prev_week) if self.prev_week else 0
        )

    @property
    def bars(self) -> list[int]:
        top = max(self.weeks) if self.weeks else 0
        return [max(4, round(30 * w / top)) if top else 4 for w in self.weeks]

    @property
    def share_without_hr(self) -> int:
        return round(100 * self.without_hr / self.total) if self.total else 0


class Analytics:
    def __init__(self, repo: Repo, ops: OpsStore, desk: HrDesk, kb: KbService) -> None:
        self.repo, self.ops, self.desk, self.kb = repo, ops, desk, kb

    def _events(self, company: str) -> list[tuple[datetime, str, bool]]:
        """(когда, тема, ответил без HR) из лога вопросов и обращений к HR без конфиденциальных."""
        ev: list[tuple[datetime, str, bool]] = []
        for q in self.ops.questions(company):
            ev.append((datetime.fromisoformat(q["at"]), q["topic"], q["status"] == "answered"))
        for r in self.desk.analytics_rows(company):
            if r["kind"] != "confidential" and r["text"]:
                ev.append((datetime.fromisoformat(r["created"]), topic_of(r["text"]), False))
        return ev

    def has_data(self, company: str) -> bool:
        return bool(self._events(company))

    def topics(self, company: str, now: datetime) -> list[TopicRow]:
        s = self.repo.settings(company)
        today = hours.to_local(now, s).date()
        wk0 = today - timedelta(days=today.weekday())
        votes: dict[str, list[int]] = {}
        for t in self.repo.tickets(company):
            if t.confidential or not t.rating:
                continue
            v = votes.setdefault(topic_of(t.text), [0, 0])
            v[0 if t.rating == "up" else 1] += 1
        acc: dict[str, list[int]] = {}
        wk: dict[str, list[int]] = {}
        for at, topic, no_hr in self._events(company):
            d = hours.to_local(at, s).date()
            idx = (wk0 - (d - timedelta(days=d.weekday()))).days // 7
            if 0 <= idx < 6:
                wk.setdefault(topic, [0] * 6)[5 - idx] += 1
            a = acc.setdefault(topic, [0, 0, 0, 0])
            a[0] += 1
            a[1] += d >= wk0
            a[2] += wk0 - timedelta(days=7) <= d < wk0
            a[3] += no_hr
        rows = [
            TopicRow(t, a[0], a[1], a[2], a[3], *votes.get(t, [0, 0]), tuple(wk.get(t, [0] * 6)))
            for t, a in acc.items()
        ]
        return sorted(rows, key=lambda r: -r.total)

    def weekly(self, company: str, now: datetime, weeks: int = 8) -> list[tuple[str, int]]:
        s = self.repo.settings(company)
        today = hours.to_local(now, s).date()
        wk0 = today - timedelta(days=today.weekday())
        counts = [0] * weeks
        for at, _, _ in self._events(company):
            d = hours.to_local(at, s).date()
            idx = (wk0 - (d - timedelta(days=d.weekday()))).days // 7
            if 0 <= idx < weeks:
                counts[idx] += 1
        return [((wk0 - timedelta(days=7 * i)).strftime("%d.%m"), counts[i]) for i in range(weeks)][
            ::-1
        ]

    def frequent_unanswered(self, company: str, limit: int = 5) -> list[tuple[str, str, int]]:
        """Частые вопросы, дошедшие до HR и ещё не решённые в базе: (номер обращения, текст, сколько раз)."""
        items = [
            (t.key, t.text)
            for t in self.repo.tickets(company)
            if not t.confidential and t.status not in ("deleted", "deleted_pending") and t.text
        ]
        groups: list[list[tuple[str, str]]] = []
        for key, tx in items:
            for g in groups:
                if similarity(g[0][1], tx) >= 0.5:
                    g.append((key, tx))
                    break
            else:
                groups.append([(key, tx)])
        out = [
            (g[0][0], g[0][1], len(g))
            for g in groups
            if not (r := self.kb.ranked(company, g[0][1])) or r[0][0] < SAME_QUESTION
        ]
        return sorted(out, key=lambda r: -r[2])[:limit]

    def topic_detail(self, company: str, topic: str) -> dict[str, Any]:
        qs = [q["masked_question"] for q in self.ops.questions(company) if q["topic"] == topic]
        tix = [
            t.text for t in self.repo.tickets(company)
            if not t.confidential and topic_of(t.text) == topic and t.text
        ]  # fmt: skip
        return {"topic": topic, "questions": [*qs, *tix][:20], "count": len(qs) + len(tix)}


def out_notice(chat_id: int, company: str, text: str, buttons: list[Button] | None = None) -> Out:
    return Out(chat_id=chat_id, company_id=company, text=text, rows=[buttons] if buttons else [])
