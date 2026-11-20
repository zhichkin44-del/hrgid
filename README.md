# HRgid

ИИ-помощник HR: ответы по Трудовому кодексу и правилам компании со ссылкой на источник.

## Telegram-бот (этап 4)

Токен бота — только из окружения (выдаёт @BotFather), в коде и в git его нет.

    export TELEGRAM_BOT_TOKEN=...        # обязательно
    export HRGID_COMPANY=<company_id>    # компания бота: для «не пришёл код» до входа
    export ANTHROPIC_API_KEY=...         # ключ модели (или провайдер HRGID_PROVIDER=gigachat|yandex)
    export DATABASE_URL=postgresql://... # иначе встроенный pgserver для разработки
    python -m hrgid.bot                  # polling

Необязательно: HRGID_RERANKER (по умолчанию mmarco-minilm), HRGID_NO_ANSWER_THRESHOLD (ADR-0006),
HRGID_CODE_PEPPER (секрет для хэша кодов приглашения).

Устройство: `hrgid/bot/engine.py` — диалоги (чистая логика, тестируется без сети),
`app.py` — доставка через aiogram, `desk.py` — очередь HR (панель этапа 6 вызывает те же методы),
`kb.py` — база ответов HR (ищется потоком вместе с документами), `calcdialog.py` — расчёты R1–R9.
Уведомления HR, ответы HR и объявления кладутся в outbox, бот доставляет их фоновой задачей.

Тесты: `pytest tests/bot` (e2e сценарии 4.1–4.8 и адаптер на фейковом Bot API).

## Панель HR (этап 6)

Запуск на наполненной компании «Студия Жичкина» (Fake-модель, ключей не нужно):

```
python scripts/run_panel.py        # http://127.0.0.1:8090, коды входа печатаются в консоль
```

Вход: `admin@studio.test` (администратор) или `hr@studio.test` (HR). Тесты панели: `pytest tests/panel`
(браузерные сценарии — маркер `browser`, нужен Chrome или `playwright install chromium`).

## MCP-сервер (этап 7)

ИИ-агенты (Claude Code, Claude Desktop) получают те же знания, что и бот, только на чтение:
`search_law` (ТК РФ со ссылкой на статью), `search_policy` (действующие правила вашей компании),
`calc` (расчёты R1–R9; у каждого своя схема входа), `check_policy_clause` (оценка пункта правил:
«соответствует / лучше закона / риск» со статьёй). Данные других компаний недоступны, ПДн в ответах
маскируются ([ФИО_1], [ТЕЛЕФОН_1] …), текст пункта уходит в модель замаскированным.

1. Администратор открывает панель: «Настройки» → «Токен для ИИ-агентов…» → «Выдать токен».
   Токен (`hrg_…`) показывается один раз, хранится хэшем, отзывается там же — действует сразу.
2. Подключение к Claude Code одной строкой:

```
claude mcp add hrgid -e HRGID_MCP_TOKEN=hrg_ВАШ_ТОКЕН -e DATABASE_URL=postgresql://... -- python -m hrgid.mcp
```

3. Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hrgid": {
      "command": "python",
      "args": ["-m", "hrgid.mcp"],
      "env": {
        "HRGID_MCP_TOKEN": "hrg_ВАШ_ТОКЕН",
        "DATABASE_URL": "postgresql://...",
        "ANTHROPIC_API_KEY": "нужен только для check_policy_clause"
      }
    }
  }
}
```

Удалённый доступ (streamable HTTP): `python -m hrgid.mcp --http --port 8765`, адрес `/mcp`,
токен в заголовке `Authorization: Bearer hrg_…` (без токена или с отозванным — 401).
Модель проверки пункта — `HRGID_PROVIDER` (claude, gigachat, yandex); без ключа остальные три
инструмента работают. Тесты: `pytest tests/mcpsrv tests/panel/test_mcp_tokens.py`.
