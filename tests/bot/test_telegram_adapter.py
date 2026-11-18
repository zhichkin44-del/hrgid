"""Адаптер aiogram на фейковом Bot API: сессия вместо сети, настоящие Update и методы."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("aiogram")

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import Message, ReplyKeyboardMarkup, Update  # noqa: E402

from hrgid.bot import app  # noqa: E402
from hrgid.bot.auth import AuthService  # noqa: E402
from hrgid.bot.desk import HrDesk  # noqa: E402
from hrgid.bot.kb import KbService  # noqa: E402
from hrgid.bot.repo import Repo  # noqa: E402

from .conftest import COMPANY, HR_CHAT, NOW, OLGA_TG, make_employee  # noqa: E402
from .harness import VACATION_ANSWER, Harness  # noqa: E402


class FakeSession(BaseSession):
    """Фейковый Bot API: запоминает вызовы, возвращает правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.forbidden: set[int] = set()

    async def close(self) -> None:
        return None

    async def stream_content(
        self, *a: Any, **k: Any
    ) -> AsyncGenerator[bytes, None]:  # pragma: no cover
        yield b""

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage):
            if int(method.chat_id or 0) in self.forbidden:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked")
            return Message.model_validate(
                {"message_id": len(self.calls), "date": datetime.now(UTC),
                 "chat": {"id": method.chat_id, "type": "private"}, "text": method.text},
                context={"bot": bot},
            )  # fmt: skip
        return True

    def sent(self, chat_id: int | None = None) -> list[SendMessage]:
        return [
            c for c in self.calls
            if isinstance(c, SendMessage) and (chat_id is None or c.chat_id == chat_id)
        ]  # fmt: skip


@pytest.fixture()
def h(repo: Repo, auth: AuthService, desk: HrDesk, kb: KbService) -> Harness:
    return Harness(repo, auth, desk, kb)


def _user(tg: int) -> dict[str, Any]:
    return {"id": tg, "is_bot": False, "first_name": "Ольга", "username": "olga"}


def _msg(tg: int, text: str | None, n: int = 1, chat_type: str = "private") -> dict[str, Any]:
    m: dict[str, Any] = {
        "message_id": n, "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": tg, "type": chat_type}, "from": _user(tg),
    }  # fmt: skip
    if text is not None:
        m["text"] = text
    else:
        m["voice"] = {"file_id": "f", "file_unique_id": "u", "duration": 1}
    return m


async def _feed(dp: Dispatcher, bot: Bot, update: dict[str, Any]) -> None:
    await dp.feed_update(bot, Update.model_validate(update, context={"bot": bot}))


def _text(tg: int, text: str | None, n: int = 1, chat_type: str = "private") -> dict[str, Any]:
    return {"update_id": n, "message": _msg(tg, text, n, chat_type)}


def _tap(tg: int, data: str, n: int = 1) -> dict[str, Any]:
    return {
        "update_id": 1000 + n,
        "callback_query": {
            "id": str(n), "from": _user(tg), "chat_instance": "ci", "data": data,
            "message": _msg(tg, "старое", 77),
        },
    }  # fmt: skip


def test_adapter_login_consent_question_and_citations(h: Harness) -> None:
    h.pipeline.on("отпуска", VACATION_ANSWER)
    emp = make_employee(h.repo)
    code = h.auth.issue_code(COMPANY, emp.id, NOW)
    session = FakeSession()
    bot = Bot("123456:TEST", session=session)
    dp = app.build_dispatcher(h.engine)

    async def scenario() -> None:
        await _feed(dp, bot, _text(OLGA_TG, "/start", 1))
        await _feed(dp, bot, _text(OLGA_TG, code, 2))
        await _feed(dp, bot, _tap(OLGA_TG, "consent:yes"))
        await _feed(dp, bot, _text(OLGA_TG, "Сколько дней отпуска мне положено?", 3))
        await _feed(dp, bot, _tap(OLGA_TG, "ans:cite:a1", 2))

    asyncio.run(scenario())
    texts = [c.text for c in session.sent(OLGA_TG)]
    assert "Введите код приглашения" in texts[0]
    assert "Код принят" in texts[1]
    assert "Согласие записано" in texts[2]
    menu_msg = session.sent(OLGA_TG)[2]
    assert isinstance(menu_msg.reply_markup, ReplyKeyboardMarkup)
    assert [k.text for row in menu_msg.reply_markup.keyboard for k in row] == [
        "Задать вопрос", "Посчитать", "Правила компании", "Позвать HR", "Мои обращения",
    ]  # fmt: skip
    assert texts[3].startswith("28 дней по закону")
    answer_markup: Any = session.sent(OLGA_TG)[3].reply_markup
    labels = [b.text for row in answer_markup.inline_keyboard for b in row]
    assert labels == ["Показать цитаты", "👍", "👎", "Спросить HR"]
    consent_markup: Any = session.sent(OLGA_TG)[1].reply_markup
    assert [b.text for row in consent_markup.inline_keyboard for b in row][:2] == [
        "Согласен", "Не согласен",
    ]  # fmt: skip
    edits = [c for c in session.calls if isinstance(c, EditMessageText)]
    assert len(edits) == 1 and "Цитаты к ответу" in (edits[0].text or "")
    assert any(isinstance(c, AnswerCallbackQuery) for c in session.calls)


def test_adapter_ignores_groups_and_explains_voice(h: Harness) -> None:
    session = FakeSession()
    bot = Bot("123456:TEST", session=session)
    dp = app.build_dispatcher(h.engine)

    async def scenario() -> None:
        await _feed(dp, bot, _text(-100, "привет", 1, chat_type="group"))
        await _feed(dp, bot, _text(OLGA_TG, None, 2))

    asyncio.run(scenario())
    assert [c.chat_id for c in session.sent()] == [OLGA_TG]
    assert "только текст" in session.sent()[0].text


def test_adapter_outbox_delivers_hr_notification_and_answer(h: Harness) -> None:
    emp = make_employee(h.repo, telegram_id=OLGA_TG, consent_at=NOW)
    session = FakeSession()
    bot = Bot("123456:TEST", session=session)
    t = h.desk.create(emp, "Можно ли взять отпуск за свой счёт?", NOW)
    h.desk.answer(COMPANY, t.key, "Анна Воронова", "Да, по заявлению.", NOW)

    n = asyncio.run(app.flush_outbox_once(bot, h.engine))
    assert n == 2
    assert "Новое обращение №101" in session.sent(HR_CHAT)[0].text
    assert "Ответ HR · обращение №101" in session.sent(OLGA_TG)[0].text
    assert asyncio.run(app.flush_outbox_once(bot, h.engine)) == 0  # повторно не шлёт


def test_adapter_blocked_bot_marks_answer_not_delivered(h: Harness) -> None:
    emp = make_employee(h.repo, telegram_id=OLGA_TG, consent_at=NOW)
    session = FakeSession()
    session.forbidden.add(OLGA_TG)
    bot = Bot("123456:TEST", session=session)
    t = h.desk.create(emp, "вопрос", NOW)
    h.desk.answer(COMPANY, t.key, "Анна", "ответ", NOW)
    asyncio.run(app.flush_outbox_once(bot, h.engine))
    assert h.desk.get(COMPANY, t.key).delivery_failed
    assert h.desk.view(h.desk.get(COMPANY, t.key), NOW).delivery_failed


def test_token_only_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        app.token_from_env()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", " 123456:TEST ")
    assert app.token_from_env() == "123456:TEST"
