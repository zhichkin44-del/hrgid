"""Движок диалогов бота (SPEC-09 §4, SCREENS §17): сообщения и кнопки -> список ответов.

Чистая логика без aiogram: ввод — (telegram_id, текст/данные кнопки, время), вывод — список Out.
Адаптер Telegram (hrgid.bot.app) только доставляет. Время передаётся снаружи.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from hrgid.bot import calcdialog, hours, texts
from hrgid.bot.auth import MAX_FAILS, AuthService, looks_like_code
from hrgid.bot.calcdialog import CalcState
from hrgid.bot.desk import DeskError, HrDesk
from hrgid.bot.kb import KbService
from hrgid.bot.models import Button, Employee, Feedback, Out, Ticket
from hrgid.bot.repo import Repo
from hrgid.flow.graph import AskRequest, FlowAnswer, Source

MENU_BUTTONS = ("Задать вопрос", "Посчитать", "Правила компании", "Позвать HR", "Мои обращения")
MAX_ANSWER = 900


class Answerer(Protocol):
    def ask(self, req: AskRequest) -> FlowAnswer: ...


@dataclass(frozen=True)
class RulesDoc:
    title: str
    version: str
    order: str
    since: date
    items: list[str]


class RulesProvider(Protocol):
    def topics(self, company_id: str) -> list[str]: ...

    def topic(self, company_id: str, topic: str) -> RulesDoc | None: ...


class NoRules:
    def topics(self, company_id: str) -> list[str]:
        return []

    def topic(self, company_id: str, topic: str) -> RulesDoc | None:
        return None


@dataclass
class Session:
    mode: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    last: datetime | None = None
    history: list[str] = field(default_factory=list)
    answers: dict[str, tuple[str, list[Source], list[list[Button]]]] = field(default_factory=dict)
    answer_seq: int = 0
    pending: tuple[str, str, str] | None = None  # (company, employee_id, code_hash)
    refused: bool = False
    calc: CalcState | None = None
    last_calc: str = ""
    last_summary: str = ""
    last_question: str = ""


def b(text: str, data: str) -> Button:
    return Button(text=text, data=data)


MENU_ROW = [b("В меню", "menu")]


class BotEngine:
    def __init__(
        self,
        repo: Repo,
        auth: AuthService,
        desk: HrDesk,
        kb: KbService,
        answerer: Answerer,
        rules: RulesProvider | None = None,
        default_company: str | None = None,
    ) -> None:
        self.repo, self.auth, self.desk, self.kb = repo, auth, desk, kb
        self.answerer = answerer
        self.rules: RulesProvider = rules or NoRules()
        self.default_company = default_company
        self.sessions: dict[int, Session] = {}
        self._companies: set[str] = set()

    # --- вход ---
    def _sess(self, tg: int) -> Session:
        return self.sessions.setdefault(tg, Session())

    def on_message(self, tg: int, text: str, now: datetime, hint: str = "") -> list[Out]:
        text = (text or "").strip()
        if not text:
            return []
        sess = self._sess(tg)
        emp = self.repo.employee_by_telegram(tg)
        if emp is None:
            return self._guest_message(tg, sess, text, now, hint)
        self._companies.add(emp.company_id)
        self.desk.finalize_company(emp.company_id, now)
        if emp.status != "active":
            return [self._closed(tg, emp)]
        out = self._timeout(tg, sess, emp, now)
        sess.last = now
        if out:
            return out
        if text.startswith("/"):
            return self._command(tg, sess, emp, text, now)
        if text in MENU_BUTTONS:
            return self._menu_action(tg, sess, emp, text, now)
        if sess.mode:
            return self._mode_input(tg, sess, emp, text, now)
        return self._question(tg, sess, emp, text, now)

    def on_callback(self, tg: int, data: str, now: datetime, hint: str = "") -> list[Out]:
        sess = self._sess(tg)
        emp = self.repo.employee_by_telegram(tg)
        if emp is None:
            return self._guest_callback(tg, sess, data, now, hint)
        self._companies.add(emp.company_id)
        self.desk.finalize_company(emp.company_id, now)
        if emp.status != "active":
            return [self._closed(tg, emp)]
        out = self._timeout(tg, sess, emp, now)
        sess.last = now
        if out:
            return out
        return self._callback(tg, sess, emp, data, now)

    def tick(self, now: datetime) -> list[Out]:
        """Фоновая проверка: таймаут расчётов (15 мин), окончательное удаление конфиденциальных."""
        outs: list[Out] = []
        for company in sorted(self._companies):
            self.desk.finalize_company(company, now)
        for tg, sess in list(self.sessions.items()):
            emp = self.repo.employee_by_telegram(tg)
            if emp is None:
                continue
            outs += self._timeout(tg, sess, emp, now)
        return outs

    # --- outbox ---
    def take_outbox(self) -> list[tuple[str, str, Out]]:
        return self.repo.outbox_pending()

    def outbox_result(self, company_id: str, key: str, out: Out, ok: bool) -> None:
        self.repo.outbox_mark(company_id, key, "sent" if ok else "failed")
        if not ok and out.meta.get("ticket"):
            self.desk.mark_delivery_failed(company_id, out.meta["ticket"])

    def flush_outbox(self, send: Callable[[Out], bool]) -> int:
        n = 0
        for company, key, out in self.take_outbox():
            ok = send(out)
            self.outbox_result(company, key, out, ok)
            n += int(ok)
        return n

    # --- помощники ---
    def _settings(self, emp: Employee) -> Any:
        return self.repo.settings(emp.company_id)

    def _closed(self, tg: int, emp: Employee) -> Out:
        s = self.repo.settings(emp.company_id)
        return Out(chat_id=tg, text=texts.closed_access(s.company_name, s.hr_name))

    def _home(self, tg: int, emp: Employee, text: str | None = None) -> Out:
        return Out(chat_id=tg, text=text or texts.menu(emp.first_name), menu=True)

    def _timeout(self, tg: int, sess: Session, emp: Employee, now: datetime) -> list[Out]:
        if sess.calc is None or sess.last is None:
            return []
        s = self.repo.settings(emp.company_id)
        if now - sess.last < timedelta(minutes=s.dialog_timeout_min):
            return []
        sess.calc = None
        sess.mode = ""
        sess.last = now
        return [
            Out(
                chat_id=tg,
                text=texts.TIMEOUT,
                rows=[[b("Посчитать", "m:calc"), b("В меню", "menu")]],
            )
        ]

    # --- гость: код и согласие ---
    def _guest_message(
        self, tg: int, sess: Session, text: str, now: datetime, hint: str
    ) -> list[Out]:
        company = self.default_company
        s = self.repo.settings(company) if company else None
        if text.startswith("/start") or (text.startswith("/") and sess.pending is None):
            sess.refused = False
            if sess.pending:
                return [self._consent_out(tg, sess)]
            name = s.company_name if s else "компании"
            return [Out(chat_id=tg, text=texts.greeting(name))]
        if sess.pending or sess.refused:
            return []  # без согласия бот не отвечает (Б1.4)
        if not looks_like_code(text):
            return [Out(chat_id=tg, text=texts.ENTER_CODE)]
        res = self.auth.check_code(tg, text, now)
        if res.status == "blocked":
            if res.fails >= MAX_FAILS:
                return [
                    Out(
                        chat_id=tg,
                        text=texts.CODE_BLOCKED,
                        rows=[
                            [
                                b("Не пришёл код", "code:missing"),
                                b("Ввести код снова", "code:again"),
                            ]
                        ],
                    )
                ]
            until = (
                hours.to_local(res.blocked_until, s).strftime("%H:%M")
                if (res.blocked_until and s)
                else ""
            )
            return [Out(chat_id=tg, text=texts.code_still_blocked(until))]
        if res.status == "bad":
            return [
                Out(
                    chat_id=tg,
                    text=texts.code_bad(res.fails, res.left),
                    rows=[[b("Не пришёл код", "code:missing")]],
                )
            ]
        assert res.employee is not None and res.code is not None
        emp = res.employee
        if res.status == "closed":
            st = self.repo.settings(emp.company_id)
            return [Out(chat_id=tg, text=texts.closed_access(st.company_name, st.hr_name))]
        self._companies.add(emp.company_id)
        if emp.consent_at is not None:  # уже давал согласие: вход, возможно с нового Telegram
            emp2, old = self.auth.bind(emp, res.code, tg, now, consent=False)
            sess.last = now
            if old is not None:
                return [
                    Out(
                        chat_id=tg,
                        text=texts.new_telegram(),
                        rows=[[b("Это я, всё верно", "login:me"), b("Это не я", "login:notme")]],
                    )
                ]
            return [self._home(tg, emp2)]
        sess.pending = (emp.company_id, emp.id, self.auth.hash_code(text))
        return [self._consent_out(tg, sess)]

    def _consent_out(self, tg: int, sess: Session, full: bool = False) -> Out:
        assert sess.pending is not None
        company, emp_id, _ = sess.pending
        emp = self.repo.employee(company, emp_id)
        s = self.repo.settings(company)
        body = (
            texts.consent_full(s.company_name)
            if full
            else texts.consent(emp.full_name if emp else "", emp.position if emp else "")
        )
        rows = [[b("Согласен", "consent:yes"), b("Не согласен", "consent:no")]]
        if not full:
            rows.append([b("Читать полностью", "consent:read")])
        return Out(chat_id=tg, text=body, rows=rows)

    def _guest_callback(
        self, tg: int, sess: Session, data: str, now: datetime, hint: str
    ) -> list[Out]:
        if data == "code:again" or data == "start":
            return [Out(chat_id=tg, text=texts.ENTER_CODE)]
        if data == "code:missing":
            return self._code_missing(tg, now, hint)
        if sess.pending is None:
            return [Out(chat_id=tg, text=texts.ENTER_CODE)]
        if data == "consent:read":
            return [self._consent_out(tg, sess, full=True)]
        if data == "consent:back":
            sess.refused = False
            return [self._consent_out(tg, sess)]
        if data == "consent:no":
            sess.refused = True
            s = self.repo.settings(sess.pending[0])
            return [
                Out(
                    chat_id=tg,
                    text=texts.not_agreed(s.hr_name),
                    rows=[[b("Вернуться к согласию", "consent:back")]],
                )
            ]
        if data == "consent:yes":
            company, emp_id, code_hash = sess.pending
            emp = self.repo.employee(company, emp_id)
            code = self.repo.code_by_hash(code_hash)
            if emp is None or code is None or code.status != "active":
                sess.pending = None
                return [Out(chat_id=tg, text="Код больше не действует. " + texts.ENTER_CODE)]
            emp2, _ = self.auth.bind(emp, code, tg, now, consent=True)
            sess.pending = None
            sess.refused = False
            s = self.repo.settings(company)
            at = hours.to_local(now, s)
            return [Out(chat_id=tg, text=texts.consent_ok(at), menu=True)]
        return [Out(chat_id=tg, text=texts.ENTER_CODE)]

    def _code_missing(self, tg: int, now: datetime, hint: str) -> list[Out]:
        company = self.default_company
        if company is None:
            return [Out(chat_id=tg, text="Напишите HR своей компании: новый код выдаёт HR.")]
        s = self.repo.settings(company)
        who = Employee(
            id=f"tg{tg}", company_id=company, full_name=hint or f"Telegram {tg}", status="active"
        )
        self.desk.create(
            who, f"Не пришёл код приглашения. Telegram: {hint or tg}", now, kind="new_code"
        )
        return [
            Out(
                chat_id=tg,
                text=texts.code_missing(s.hr_name, hours.hours_range(s)),
                rows=[[b("Ввести код", "code:again"), b("В начало", "start")]],
            )
        ]

    # --- команды и меню ---
    def _command(
        self, tg: int, sess: Session, emp: Employee, text: str, now: datetime
    ) -> list[Out]:
        cmd = text.split()[0].split("@")[0].lower()
        sess.mode = ""
        sess.calc = None
        if cmd in ("/start", "/menu", "/cancel"):
            return [self._home(tg, emp)]
        if cmd == "/calc":
            return self._calc_picker(tg)
        if cmd == "/hr":
            return self._hr_menu(tg, emp, now)
        if cmd == "/rules":
            return self._rules_menu(tg, emp)
        if cmd == "/privacy":
            return [
                Out(
                    chat_id=tg,
                    text=texts.PRIVACY,
                    rows=[[b("Удалить мои данные", "priv:del")], MENU_ROW],
                )
            ]
        if cmd == "/help":
            return [Out(chat_id=tg, text=texts.HELP, rows=[MENU_ROW])]
        return [self._home(tg, emp)]

    def _menu_action(
        self, tg: int, sess: Session, emp: Employee, text: str, now: datetime
    ) -> list[Out]:
        sess.mode = ""
        sess.calc = None
        if text == "Задать вопрос":
            sess.mode = "ask"
            return [Out(chat_id=tg, text=texts.ASK_PROMPT, rows=[[b("Отмена", "menu")]])]
        if text == "Посчитать":
            return self._calc_picker(tg)
        if text == "Правила компании":
            return self._rules_menu(tg, emp)
        if text == "Позвать HR":
            return self._hr_menu(tg, emp, now)
        return self._my_tickets(tg, emp)

    def _calc_picker(self, tg: int) -> list[Out]:
        return [Out(chat_id=tg, text=calcdialog.PICKER_TEXT, rows=calcdialog.picker_rows())]

    # --- режимы ввода ---
    def _mode_input(
        self, tg: int, sess: Session, emp: Employee, text: str, now: datetime
    ) -> list[Out]:
        mode = sess.mode
        if text.lower() in ("отмена", "cancel"):
            sess.mode, sess.calc = "", None
            return [self._home(tg, emp, texts.CANCELLED)]
        if mode == "calc" and sess.calc is not None:
            return self._calc_step(tg, sess, emp, text, now, callback=False)
        if mode == "ask":
            sess.mode = ""
            return self._question(tg, sess, emp, text, now)
        if mode in ("hr_text", "conf_text"):
            sess.mode = ""
            return self._create_ticket(tg, emp, text, now, confidential=mode == "conf_text")
        if mode == "append":
            sess.mode = ""
            return self._append(tg, emp, str(sess.data.get("key", "")), text, now)
        if mode == "dates":
            sess.mode = ""
            ann = self.repo.announcement(emp.company_id, str(sess.data.get("ann", "")))
            title = ann.title if ann else "объявление"
            self.desk.create(emp, f"Желаемые даты отпуска («{title}»): {text}", now, kind="dates")
            return [Out(chat_id=tg, text=texts.DATES_ACCEPTED, rows=[MENU_ROW])]
        sess.mode = ""
        return self._question(tg, sess, emp, text, now)

    # --- вопрос -> поток этапа 2 ---
    def _question(
        self, tg: int, sess: Session, emp: Employee, text: str, now: datetime
    ) -> list[Out]:
        sess.last_question = text
        same = self.kb.find_same(emp.company_id, text, emp.group)
        if same is not None:
            body = texts.kb_answer(same.answer, same.basis, same.author or "HR", same.created_at)
            self._remember(sess, text)
            return [
                Out(
                    chat_id=tg,
                    text=body,
                    rows=[
                        [b("👍", "ans:up:kb"), b("👎", "ans:down:kb")],
                        [b("Спросить HR", "ans:hr:kb"), b("В меню", "menu")],
                    ],
                )
            ]
        try:
            ans = self.answerer.ask(
                AskRequest(text, emp.company_id, emp.group, tuple(sess.history[-6:]), None)
            )
        except Exception:
            return self._failure(tg, emp, text, now)
        if ans.status == "error":
            return self._failure(tg, emp, text, now)
        self._remember(sess, text)
        if ans.status in ("no_answer", "unverified"):
            body = texts.NO_ANSWER if ans.status == "no_answer" else texts.UNVERIFIED
            return [self._offer_hr(tg, body, now, emp)]
        if ans.status == "off_topic":
            return [
                Out(
                    chat_id=tg,
                    text=texts.OFF_TOPIC,
                    rows=[
                        [b("Задать кадровый вопрос", "m:ask")],
                        [b("Позвать HR", "m:hr"), b("В меню", "menu")],
                    ],
                )
            ]
        if ans.status == "to_hr":
            return [self._offer_hr(tg, texts.TO_HR_OFFER, now, emp)]
        return [self._answer_out(tg, sess, emp, ans, now)]

    def _remember(self, sess: Session, text: str) -> None:
        sess.history.append(text)
        del sess.history[:-6]  # диалог учитывает 6 последних сообщений (§4.4)

    def _offer_hr(self, tg: int, body: str, now: datetime, emp: Employee) -> Out:
        s = self.repo.settings(emp.company_id)
        if not hours.is_open(now, s):
            body += (
                f"\nСейчас вне часов HR: {hours.hours_range(s)} — это {hours.open_phrase(now, s)}."
            )
            if s.urgent_contact:
                body += f"\nСрочно: {s.urgent_contact}."
        return Out(
            chat_id=tg,
            text=body,
            rows=[[b("Передать HR", "noans:hr"), b("Спросить иначе", "noans:rephrase")]],
        )

    def _failure(self, tg: int, emp: Employee, question: str, now: datetime) -> list[Out]:
        t = self.desk.create(emp, question, now, kind="failure")
        s = self.repo.settings(emp.company_id)
        return [
            Out(
                chat_id=tg,
                text=texts.service_failure(t.label, s.hr_name),
                rows=[[b("Правила компании", "m:rules"), b("Посчитать", "m:calc")], MENU_ROW],
            )
        ]

    def _answer_out(
        self, tg: int, sess: Session, emp: Employee, ans: FlowAnswer, now: datetime
    ) -> Out:
        s = self.repo.settings(emp.company_id)
        body = ans.text.strip()
        if len(body) > MAX_ANSWER:
            body = body[: MAX_ANSWER - 1].rstrip() + "…"
        if ans.sources:
            refs = "; ".join(dict.fromkeys(src.ref for src in ans.sources))
            body += f"\n\nИсточник: {refs}"
        today = hours.to_local(now, s).date()
        if emp.disclaimer_date != today:  # «не юридическая консультация» — в первом ответе дня
            body += f"\n\n{texts.DISCLAIMER}"
            emp = emp.model_copy(update={"disclaimer_date": today})
            self.repo.save_employee(emp)
        sess.answer_seq += 1
        aid = f"a{sess.answer_seq}"
        rows: list[list[Button]] = []
        if any(src.quote for src in ans.sources):
            rows.append([b("Показать цитаты", f"ans:cite:{aid}")])
        rows.append(
            [
                b("👍", f"ans:up:{aid}"),
                b("👎", f"ans:down:{aid}"),
                b("Спросить HR", f"ans:hr:{aid}"),
            ]
        )
        sess.answers[aid] = (body, list(ans.sources), rows)
        for old in list(sess.answers)[:-20]:
            del sess.answers[old]
        sess.data[f"route:{aid}"] = ans.route
        return Out(chat_id=tg, text=body, rows=rows)

    # --- расчёты ---
    def _calc_start(
        self, tg: int, sess: Session, emp: Employee, code: str, now: datetime
    ) -> list[Out]:
        s = self.repo.settings(emp.company_id)
        today = hours.to_local(now, s).date()
        sess.calc, reply = calcdialog.start(code, emp, today)
        sess.mode = "calc"
        sess.last_calc = code
        return [Out(chat_id=tg, text=reply.text, rows=reply.rows)]

    def _calc_step(
        self,
        tg: int,
        sess: Session,
        emp: Employee,
        text: str,
        now: datetime,
        *,
        callback: bool,
    ) -> list[Out]:
        assert sess.calc is not None
        s = self.repo.settings(emp.company_id)
        today = hours.to_local(now, s).date()
        reply = calcdialog.advance(sess.calc, emp, today, text, callback=callback)
        if reply.cancelled:
            sess.calc, sess.mode = None, ""
            return [self._home(tg, emp, texts.CANCELLED)]
        if reply.done:
            sess.calc, sess.mode = None, ""
            sess.last_summary = reply.summary
        return [Out(chat_id=tg, text=reply.text, rows=reply.rows)]

    # --- правила компании ---
    def _rules_menu(self, tg: int, emp: Employee) -> list[Out]:
        topics = self.rules.topics(emp.company_id)
        if not topics:
            return [Out(chat_id=tg, text=texts.RULES_NONE, rows=[MENU_ROW])]
        rows = [[b(t.capitalize(), f"rules:t:{t}")] for t in topics]
        return [Out(chat_id=tg, text=texts.RULES_PICK, rows=[*rows, MENU_ROW])]

    def _rules_topic(self, tg: int, emp: Employee, topic: str) -> list[Out]:
        doc = self.rules.topic(emp.company_id, topic)
        if doc is None:
            return [Out(chat_id=tg, text=texts.RULES_NONE, rows=[MENU_ROW])]
        body = (
            f"{doc.title}\nВерсия {doc.version}, приказ {doc.order}, "
            f"действует с {doc.since:%d.%m.%Y}.\n\n" + "\n".join(doc.items)
        )
        return [
            Out(
                chat_id=tg,
                text=body,
                rows=[
                    [b("Задать вопрос по этому правилу", "m:ask")],
                    [b("Другая тема", "m:rules"), b("В меню", "menu")],
                ],
            )
        ]

    # --- обращения к HR ---
    def _hr_menu(self, tg: int, emp: Employee, now: datetime) -> list[Out]:
        s = self.repo.settings(emp.company_id)
        if hours.is_open(now, s):
            return [
                Out(
                    chat_id=tg,
                    text=texts.hr_choose(f"{s.hours_end:%H:%M}"),
                    rows=[
                        [b("Обычный вопрос к HR", "hr:plain")],
                        [b("Конфиденциально", "hr:conf")],
                        [b("Отмена", "menu")],
                    ],
                )
            ]
        return [
            Out(
                chat_id=tg,
                text=texts.hr_closed(
                    s.hr_name, hours.hours_range(s), hours.open_phrase(now, s), s.urgent_contact
                ),
                rows=[
                    [b("Оставить обращение", "hr:plain")],
                    [b("Конфиденциально", "hr:conf")],
                    MENU_ROW,
                ],
            )
        ]

    def _accepted(self, tg: int, emp: Employee, t: Ticket, now: datetime) -> Out:
        s = self.repo.settings(emp.company_id)
        in_hours = not t.out_of_hours
        note = ""
        if not in_hours:
            note = f"Сейчас вне часов HR ({hours.hours_range(s)})."
            if s.urgent_contact:
                note += f" Срочно: {s.urgent_contact}."
        text = texts.ticket_accepted(
            t.label, s.sla_hours, hours.deadline_phrase(now, t.due_at, s), note, in_hours
        )
        return Out(
            chat_id=tg, text=text, rows=[[b("Мои обращения", "m:mine"), b("В меню", "menu")]]
        )

    def _create_ticket(
        self, tg: int, emp: Employee, text: str, now: datetime, *, confidential: bool
    ) -> list[Out]:
        t = self.desk.create(emp, text, now, confidential=confidential)
        if confidential:
            rows = [
                [b("Раскрыть имя", f"conf:reveal:{t.key}")],
                [b("Дописать", f"conf:add:{t.key}"), b("Удалить обращение", f"conf:del:{t.key}")],
            ]
            return [Out(chat_id=tg, text=texts.conf_accepted(t.label), rows=rows)]
        return [self._accepted(tg, emp, t, now)]

    def _append(self, tg: int, emp: Employee, key: str, text: str, now: datetime) -> list[Out]:
        t = self.repo.ticket(emp.company_id, key)
        if t is None or t.employee_id != emp.id:
            return [self._home(tg, emp)]
        try:
            self.desk.append(t, text, now)
        except DeskError as e:
            return [Out(chat_id=tg, text=str(e), rows=[MENU_ROW])]
        return [Out(chat_id=tg, text=texts.APPENDED, rows=[MENU_ROW])]

    def _mine(self, emp: Employee, key: str) -> Ticket | None:
        t = self.repo.ticket(emp.company_id, key)
        if t is None or t.employee_id != emp.id:  # чужое обращение недоступно
            return None
        return t

    def _my_tickets(self, tg: int, emp: Employee) -> list[Out]:
        mine = self.desk.employee_tickets(emp)[:6]
        if not mine:
            return [
                Out(chat_id=tg, text=texts.NO_TICKETS, rows=[[b("Позвать HR", "m:hr")], MENU_ROW])
            ]
        status = {"queue": "в очереди", "answered": "отвечено", "closed": "закрыто",
                  "withdrawn": "отозвано"}  # fmt: skip
        lines = [
            f"{t.label} · {t.created_at:%d.%m} · {t.text[:40]} — {status.get(t.status, t.status)}"
            for t in mine
        ]
        btns = [b(f"Открыть {t.label}", f"t:open:{t.key}") for t in mine]
        rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
        rows.append(MENU_ROW)
        return [Out(chat_id=tg, text="Ваши обращения\n" + "\n".join(lines), rows=rows)]

    def _open_ticket(self, tg: int, emp: Employee, key: str, now: datetime) -> list[Out]:
        t = self._mine(emp, key)
        if t is None or t.status in ("deleted", "deleted_pending"):
            return [self._home(tg, emp)]
        s = self.repo.settings(emp.company_id)
        if t.status == "queue":
            created = hours.to_local(t.created_at, s)
            body = texts.ticket_card(
                t.label,
                f"{created:%d.%m, %H:%M}",
                t.text,
                t.assignee,
                hours.deadline_phrase(now, t.due_at, s),
            )
            rows = (
                [
                    [b("Раскрыть имя", f"conf:reveal:{t.key}")],
                    [
                        b("Дописать", f"conf:add:{t.key}"),
                        b("Удалить обращение", f"conf:del:{t.key}"),
                    ],
                ]
                if t.confidential
                else [
                    [b("Дописать", f"t:add:{t.key}"), b("Отозвать обращение", f"t:wd:{t.key}")],
                    [b("Мои обращения", "m:mine")],
                ]
            )
            return [Out(chat_id=tg, text=body, rows=rows)]
        hr_msgs = [m for m in t.messages if m.author == "hr"]
        if t.status == "answered" and hr_msgs:
            m = hr_msgs[-1]
            return [
                Out(
                    chat_id=tg,
                    text=f"Ответ HR · обращение {t.label}\n{m.hr_user or s.hr_name}, HR\n\n"
                    f"{m.text}\n\nОтвет оказался полезным?",
                    rows=[[b("👍", f"hrr:up:{t.key}"), b("👎", f"hrr:down:{t.key}")]],
                )
            ]
        label = {"closed": "закрыто", "withdrawn": "отозвано"}.get(t.status, t.status)
        return [Out(chat_id=tg, text=f"Обращение {t.label} — {label}.", rows=[MENU_ROW])]

    # --- кнопки ---
    def _callback(
        self, tg: int, sess: Session, emp: Employee, data: str, now: datetime
    ) -> list[Out]:
        head, _, rest = data.partition(":")
        if data == "menu":
            sess.mode, sess.calc = "", None
            return [self._home(tg, emp)]
        if head == "m":
            return self._menu_action(
                tg, sess, emp,
                {"ask": "Задать вопрос", "calc": "Посчитать", "rules": "Правила компании",
                 "hr": "Позвать HR", "mine": "Мои обращения"}.get(rest, ""),
                now,
            )  # fmt: skip
        if head in ("calc", "cs"):
            return self._cb_calc(tg, sess, emp, data, now)
        if head == "rules":
            return self._rules_topic(tg, emp, rest.removeprefix("t:"))
        if head == "hr":
            sess.mode = "hr_text" if rest == "plain" else "conf_text"
            s = self.repo.settings(emp.company_id)
            text = texts.hr_prompt(s.hr_name) if rest == "plain" else texts.CONF_PROMPT
            return [Out(chat_id=tg, text=text, rows=[[b("Отмена", "menu")]])]
        if head == "ans":
            return self._cb_answer(tg, sess, emp, rest, now)
        if head == "noans":
            if rest == "rephrase":
                sess.mode = "ask"
                return [Out(chat_id=tg, text=texts.ASK_PROMPT, rows=[[b("Отмена", "menu")]])]
            return self._create_ticket(tg, emp, sess.last_question or "Вопрос без текста", now,
                                       confidential=False)  # fmt: skip
        if head == "t":
            return self._cb_ticket(tg, sess, emp, rest, now)
        if head == "hrr":
            return self._cb_hr_rating(tg, emp, rest)
        if head == "conf":
            return self._cb_conf(tg, sess, emp, rest, now)
        if head == "ann":
            return self._cb_announcement(tg, sess, emp, rest, now)
        if head == "priv":
            return self._cb_privacy(tg, emp, rest, now)
        if head == "login":
            return self._cb_login(tg, emp, rest, now)
        return [self._home(tg, emp)]

    def _cb_calc(
        self, tg: int, sess: Session, emp: Employee, data: str, now: datetime
    ) -> list[Out]:
        if data == "calc:cancel":
            sess.calc, sess.mode = None, ""
            return [self._home(tg, emp, texts.CANCELLED)]
        if data == "calc:other":
            sess.calc, sess.mode = None, ""
            return self._calc_picker(tg)
        if data == "calc:redo" and sess.last_calc:
            return self._calc_start(tg, sess, emp, sess.last_calc, now)
        if data == "calc:hr":
            return self._create_ticket(
                tg, emp, f"Вопрос по расчёту: {sess.last_summary or sess.last_calc}", now,
                confidential=False,
            )  # fmt: skip
        if data in ("calc:up", "calc:down"):
            self._feedback(emp, "up" if data.endswith("up") else "down", "calc", now)
            return [Out(chat_id=tg, text=texts.RATING_THANKS, rows=[MENU_ROW])]
        if data.startswith("calc:R") and data[5:] in calcdialog.TITLES:
            return self._calc_start(tg, sess, emp, data[5:], now)
        if data.startswith("cs:") and sess.calc is not None:
            return self._calc_step(tg, sess, emp, data, now, callback=True)
        return self._calc_picker(tg)

    def _feedback(self, emp: Employee, vote: str, route: str, now: datetime) -> None:
        import secrets

        self.repo.save_feedback(
            Feedback(
                id=secrets.token_hex(6),
                company_id=emp.company_id,
                employee_id=emp.id,
                vote="up" if vote == "up" else "down",
                route=route,
                at=now,
            )
        )

    def _cb_answer(
        self, tg: int, sess: Session, emp: Employee, rest: str, now: datetime
    ) -> list[Out]:
        action, _, aid = rest.partition(":")
        if action in ("up", "down"):
            self._feedback(emp, action, str(sess.data.get(f"route:{aid}", "kb")), now)
            rows = [[b("Спросить HR", f"ans:hr:{aid}"), b("В меню", "menu")]] if action == "down" \
                else [MENU_ROW]  # fmt: skip
            return [Out(chat_id=tg, text=texts.RATING_THANKS, rows=rows)]
        if action == "hr":
            return self._create_ticket(
                tg, emp, sess.last_question or "Вопрос без текста", now, confidential=False
            )
        if aid not in sess.answers:
            return [self._home(tg, emp)]
        body, sources, rows = sess.answers[aid]
        if action == "cite":
            quotes = "\n\n".join(f"«{s.quote}» — {s.ref}" for s in sources if s.quote)
            text = f"{body}\n\nЦитаты к ответу (проверены по тексту документов):\n{quotes}"
            return [Out(chat_id=tg, text=text, rows=[[b("Свернуть", f"ans:hide:{aid}")], *rows[1:]],
                        edit=True)]  # fmt: skip
        return [Out(chat_id=tg, text=body, rows=rows, edit=True)]

    def _cb_ticket(
        self, tg: int, sess: Session, emp: Employee, rest: str, now: datetime
    ) -> list[Out]:
        action, _, key = rest.partition(":")
        if action == "open":
            return self._open_ticket(tg, emp, key, now)
        t = self._mine(emp, key)
        if t is None:
            return [self._home(tg, emp)]
        if action == "add":
            sess.mode, sess.data["key"] = "append", key
            return [Out(chat_id=tg, text=texts.APPEND_PROMPT, rows=[[b("Отмена", "menu")]])]
        if action == "wd":
            return [
                Out(
                    chat_id=tg,
                    text=texts.WITHDRAW_CONFIRM,
                    rows=[[b("Да, отозвать", f"t:wdy:{key}"), b("Нет, оставить", f"t:open:{key}")]],
                )
            ]
        if action == "wdy":
            try:
                self.desk.withdraw(t)
            except DeskError as e:
                return [Out(chat_id=tg, text=str(e), rows=[MENU_ROW])]
            return [
                Out(
                    chat_id=tg,
                    text=texts.WITHDRAWN,
                    rows=[[b("Мои обращения", "m:mine")], MENU_ROW],
                )
            ]
        return [self._home(tg, emp)]

    def _cb_hr_rating(self, tg: int, emp: Employee, rest: str) -> list[Out]:
        vote, _, key = rest.partition(":")
        t = self._mine(emp, key)
        if t is None or vote not in ("up", "down"):
            return [self._home(tg, emp)]
        try:
            t = self.desk.rate(t, "up" if vote == "up" else "down")
        except DeskError:
            return [Out(chat_id=tg, text=texts.RATING_THANKS, rows=[MENU_ROW])]
        return [Out(chat_id=tg, text=texts.hr_rated(t.label), rows=[MENU_ROW])]

    def _cb_conf(
        self, tg: int, sess: Session, emp: Employee, rest: str, now: datetime
    ) -> list[Out]:
        action, _, key = rest.partition(":")
        t = self._mine(emp, key)
        if t is None or not t.confidential:
            return [self._home(tg, emp)]
        if action == "reveal":
            return [
                Out(
                    chat_id=tg,
                    text=texts.CONF_REVEAL_ASK,
                    rows=[[b("Да, раскрыть", f"conf:ry:{key}"),
                           b("Нет, остаться анонимным", f"conf:rn:{key}")]],
                )
            ]  # fmt: skip
        if action == "ry":
            self.desk.reveal(t)
            s = self.repo.settings(emp.company_id)
            if s.hr_chat_id is not None:
                self.repo.enqueue(
                    Out(
                        chat_id=s.hr_chat_id,
                        company_id=emp.company_id,
                        text=f"Автор конфиденциального обращения {t.label} раскрыл имя: "
                        f"{emp.full_name}.",
                    ),
                    now,
                )
            return [Out(chat_id=tg, text=texts.CONF_REVEALED, rows=[MENU_ROW])]
        if action == "rn":
            return [self._home(tg, emp)]
        if action == "add":
            sess.mode, sess.data["key"] = "append", key
            return [Out(chat_id=tg, text=texts.APPEND_PROMPT, rows=[[b("Отмена", "menu")]])]
        if action == "del":
            return [
                Out(
                    chat_id=tg,
                    text=texts.CONF_DELETE_ASK,
                    rows=[
                        [b("Да, удалить", f"conf:dy:{key}"), b("Нет, оставить", f"conf:dn:{key}")]
                    ],
                )
            ]
        if action == "dy":
            t = self.desk.request_delete(t, now)
            s = self.repo.settings(emp.company_id)
            return [
                Out(
                    chat_id=tg,
                    text=texts.conf_deleted(t.label, s.delete_undo_sec),
                    rows=[[b("Вернуть обращение", f"conf:undo:{key}")], MENU_ROW],
                )
            ]
        if action == "undo":
            back = self.desk.undo_delete(t, now)
            if back is None:
                return [Out(chat_id=tg, text=texts.CONF_GONE, rows=[MENU_ROW])]
            return [Out(chat_id=tg, text=texts.CONF_RESTORED, rows=[MENU_ROW])]
        return [self._home(tg, emp)]

    def _cb_announcement(
        self, tg: int, sess: Session, emp: Employee, rest: str, now: datetime
    ) -> list[Out]:
        action, _, ann_id = rest.partition(":")
        if action == "read":
            self.desk.mark_read(emp.company_id, ann_id, emp.id, now)
            return [Out(chat_id=tg, text=texts.READ_MARKED, rows=[MENU_ROW])]
        if action == "dates":
            self.desk.mark_read(emp.company_id, ann_id, emp.id, now)
            sess.mode, sess.data["ann"] = "dates", ann_id
            return [Out(chat_id=tg, text=texts.DATES_PROMPT, rows=[[b("Отмена", "menu")]])]
        return [self._home(tg, emp)]

    def _cb_privacy(self, tg: int, emp: Employee, rest: str, now: datetime) -> list[Out]:
        if rest == "del":
            return [
                Out(
                    chat_id=tg,
                    text=texts.DELETE_CONFIRM,
                    rows=[[b("Да, удалить", "priv:dy"), b("Отмена", "menu")]],
                )
            ]
        if rest == "dy":
            self.desk.create(
                emp, f"Запрос на удаление данных сотрудника {emp.full_name}", now,
                kind="delete_request",
            )  # fmt: skip
            return [Out(chat_id=tg, text=texts.DELETE_ACCEPTED, rows=[MENU_ROW])]
        return [self._home(tg, emp)]

    def _cb_login(self, tg: int, emp: Employee, rest: str, now: datetime) -> list[Out]:
        if rest == "me":
            return [self._home(tg, emp)]
        if rest == "notme":
            self.desk.create(
                emp, f"Сигнал: вход по коду {emp.full_name} с нового Telegram — сотрудник "
                "сообщает, что это не он. Код отозван.", now, kind="wrong_login",
            )  # fmt: skip
            self.auth.revoke_codes(emp.company_id, emp.id)
            self.auth.unbind(emp)
            return [Out(chat_id=tg, text=texts.NOT_ME)]
        return [self._home(tg, emp)]
