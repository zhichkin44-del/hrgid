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
