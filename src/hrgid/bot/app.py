"""Адаптер Telegram (aiogram 3): доставка сообщений движка, outbox, фоновые задачи, запуск.

Токен — только из окружения TELEGRAM_BOT_TOKEN. Запуск: `python -m hrgid.bot` (см. README).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from hrgid.bot.engine import MENU_BUTTONS, BotEngine
from hrgid.bot.models import Out

log = logging.getLogger("hrgid.bot")
TG_LIMIT = 4096


def inline_markup(out: Out) -> InlineKeyboardMarkup | None:
    if not out.rows:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=b.text, callback_data=b.data) for b in row]
            for row in out.rows
        ]
    )


def menu_markup() -> ReplyKeyboardMarkup:
    keys = [[KeyboardButton(text=t) for t in MENU_BUTTONS[:3]],
            [KeyboardButton(text=t) for t in MENU_BUTTONS[3:]]]  # fmt: skip
    return ReplyKeyboardMarkup(
        keyboard=keys, resize_keyboard=True, input_field_placeholder="/start — меню"
    )


async def send_out(bot: Bot, out: Out, edit: Message | None = None) -> None:
    text = out.text[:TG_LIMIT]
    if out.edit and edit is not None:
        await edit.edit_text(text, reply_markup=inline_markup(out))
        return
    if out.menu:
        # клавиатура меню — к первому сообщению; инлайн-кнопки (если есть) отдельным сообщением
        await bot.send_message(out.chat_id, text, reply_markup=menu_markup())
        return
    await bot.send_message(out.chat_id, text, reply_markup=inline_markup(out))


def _hint(user: Any) -> str:
    if user is None:
        return ""
    name = " ".join(x for x in (user.first_name, user.last_name) if x)
    return f"{name} (@{user.username})" if user.username else name


def build_dispatcher(engine: BotEngine) -> Dispatcher:
    dp = Dispatcher()

    @dp.message()
    async def on_message(message: Message, bot: Bot) -> None:
        if message.chat.type != "private" or message.from_user is None:
            return
        if not message.text:  # голосовые и вложения — v2 (§4.9)
            await bot.send_message(
                message.chat.id, "Пока понимаю только текст. Напишите вопрос словами."
            )
            return
        outs = await asyncio.to_thread(
            engine.on_message,
            message.from_user.id,
            message.text,
            datetime.now(UTC),
            _hint(message.from_user),
        )
        for out in outs:
            await send_out(bot, out)

    @dp.callback_query()
    async def on_callback(cq: CallbackQuery, bot: Bot) -> None:
        await cq.answer()
        if cq.data is None or cq.message is None or not isinstance(cq.message, Message):
            return
        outs = await asyncio.to_thread(
            engine.on_callback, cq.from_user.id, cq.data, datetime.now(UTC), _hint(cq.from_user)
        )
        for out in outs:
            await send_out(bot, out, edit=cq.message)

    return dp


async def flush_outbox_once(bot: Bot, engine: BotEngine) -> int:
    """Доставить накопленное в outbox (уведомления HR, ответы HR, объявления)."""
    sent = 0
    for company, key, out in await asyncio.to_thread(engine.take_outbox):
        ok = True
        try:
            await send_out(bot, out)
        except TelegramAPIError as e:  # бот заблокирован, чат не найден и т.п.
            log.warning("не доставлено %s: %s", key, e)
            ok = False
        await asyncio.to_thread(engine.outbox_result, company, key, out, ok)
        sent += int(ok)
    return sent


async def background(
    bot: Bot, engine: BotEngine, outbox_every: float = 3, tick_every: float = 30
) -> None:
    last_tick = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            await flush_outbox_once(bot, engine)
            if loop.time() - last_tick >= tick_every:
                last_tick = loop.time()
                for out in await asyncio.to_thread(engine.tick, datetime.now(UTC)):
                    await send_out(bot, out)
        except Exception:
            log.exception("фоновая задача бота")
        await asyncio.sleep(outbox_every)


async def run_polling(token: str, engine: BotEngine) -> None:
    bot = Bot(token)
    dp = build_dispatcher(engine)
    task = asyncio.create_task(background(bot, engine))
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        await bot.session.close()


def token_from_env() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN в окружении (токен выдаёт @BotFather).")
    return token


def build_engine_from_env() -> BotEngine:
    """Боевая сборка: PostgreSQL, поиск по закону + база ответов HR, модель Claude с запасными."""
    from hrgid.bot.auth import AuthService
    from hrgid.bot.desk import HrDesk
    from hrgid.bot.kb import KbSearcher, KbService
    from hrgid.bot.repo import Repo
    from hrgid.bot.store import PgDocStore
    from hrgid.db import connect, database_url, init_schema
    from hrgid.flow.build import make_client, make_searcher
    from hrgid.flow.graph import FlowConfig, Pipeline
    from hrgid.llm.resilient import ResilientLLM

    conn = connect(database_url())
    init_schema(conn)
    store = PgDocStore(conn)
    store.init_schema()
    repo = Repo(store)
    kb = KbService(repo)
    llm = ResilientLLM(make_client(os.environ.get("HRGID_PROVIDER", "claude")))
    reranker = os.environ.get("HRGID_RERANKER", "mmarco-minilm")
    searcher = KbSearcher(make_searcher(conn, reranker), kb)
    threshold = os.environ.get("HRGID_NO_ANSWER_THRESHOLD")
    pipeline = Pipeline(
        llm, llm, searcher,
        config=FlowConfig(no_answer_threshold=float(threshold) if threshold else None),
    )  # fmt: skip
    return BotEngine(
        repo, AuthService(repo), HrDesk(repo, kb), kb, pipeline,
        default_company=os.environ.get("HRGID_COMPANY"),
    )  # fmt: skip


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = token_from_env()
    asyncio.run(run_polling(token, build_engine_from_env()))
