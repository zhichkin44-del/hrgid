from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from hrgid.bot.auth import AuthService
from hrgid.bot.desk import HrDesk
from hrgid.bot.engine import BotEngine, RulesDoc
from hrgid.bot.kb import KbService
from hrgid.bot.models import Out
from hrgid.bot.repo import Repo
from hrgid.flow.graph import AskRequest, FlowAnswer, Source

from .conftest import COMPANY, NOW


class FakePipeline:
    """Поток этапа 2 заменён скриптом: ключевое слово вопроса -> готовый FlowAnswer."""

    def __init__(self) -> None:
        self.script: list[tuple[str, FlowAnswer | Exception]] = []
        self.requests: list[AskRequest] = []

    def on(self, word: str, ans: FlowAnswer | Exception) -> None:
        self.script.append((word, ans))

    def ask(self, req: AskRequest) -> FlowAnswer:
        self.requests.append(req)
        for word, ans in self.script:
            if word in req.question:
                if isinstance(ans, Exception):
                    raise ans
                return ans
        return FlowAnswer("no_answer", "")


class FakeRules:
    def topics(self, company_id: str) -> list[str]:
        return ["отпуск"] if company_id == COMPANY else []

    def topic(self, company_id: str, topic: str) -> RulesDoc | None:
        if company_id != COMPANY or topic != "отпуск":
            return None
        from datetime import date

        return RulesDoc("Правила Студии — отпуск", "3", "№12", date(2026, 1, 1),
                        ["4.1. Отпуск — 31 календарный день."])  # fmt: skip


VACATION_ANSWER = FlowAnswer(
    "answered",
    "28 дней по закону, в Студии — 31.",
    "law",
    [
        Source(1, "ст. 115 ТК РФ", "115", "Ежегодный основной оплачиваемый отпуск — 28 дней.", COMPANY),
        Source(2, "Правила Студии, п. 4.1", "4.1", "Отпуск — 31 календарный день.", COMPANY),
    ],
)


@dataclass
class Chat:
    h: Harness
    tg: int
    inbox: list[Out] = field(default_factory=list)

    @property
    def last(self) -> Out:
        return self.inbox[-1]

    @property
    def text(self) -> str:
        return self.last.text

    @property
    def buttons(self) -> list[str]:
        return [b.text for row in self.last.rows for b in row]

    def say(self, text: str) -> Chat:
        outs = self.h.engine.on_message(self.tg, text, self.h.now)
        self.inbox += [o for o in outs if o.chat_id == self.tg]
        return self

    def tap(self, label: str) -> Chat:
        for row in self.last.rows:
            for b in row:
                if b.text == label:
                    return self.data(b.data)
        raise AssertionError(f"нет кнопки «{label}», есть: {self.buttons}")

    def data(self, data: str) -> Chat:
        outs = self.h.engine.on_callback(self.tg, data, self.h.now)
        self.inbox += [o for o in outs if o.chat_id == self.tg]
        return self

    def pushes(self) -> list[Out]:
        """Доставить outbox (как делает адаптер) и вернуть то, что пришло в этот чат."""
        got: list[Out] = []

        def send(o: Out) -> bool:
            self.h.sent.append(o)
            if o.chat_id == self.tg:
                got.append(o)
            return True

        self.h.engine.flush_outbox(send)
        self.inbox += got
        return got


class Harness:
    def __init__(self, repo: Repo, auth: AuthService, desk: HrDesk, kb: KbService) -> None:
        self.repo, self.auth, self.desk, self.kb = repo, auth, desk, kb
        self.pipeline = FakePipeline()
        self.engine = BotEngine(
            repo, auth, desk, kb, self.pipeline, FakeRules(), default_company=COMPANY
        )
        self.now: datetime = NOW
        self.sent: list[Out] = []

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)

    def chat(self, tg: int) -> Chat:
        return Chat(self, tg)

    def hr_inbox(self) -> list[Out]:
        return [o for o in self.sent if o.chat_id == 9001]
