"""Точка входа контейнеров: `python -m hrgid.ops.launch <роль>`.

Роли: init, api, worker, bot, mcp. Бот без токена и MCP без модуля (этап 7 ещё не слит) не падают,
а честно пишут причину и ждут: `docker compose up` остаётся зелёным, а причина видна в логе.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import runpy
import sys
import threading

log = logging.getLogger("hrgid.ops.launch")
ROLES = ("init", "api", "worker", "bot", "mcp")


def idle(reason: str) -> None:
    log.warning("%s — сервис ждёт (исправьте и перезапустите: docker compose up -d)", reason)
    threading.Event().wait()


def run(role: str) -> None:
    from hrgid.ops.config import apply_secrets

    if role not in ROLES:
        raise SystemExit(f"роль: одна из {ROLES}")
    apply_secrets()
    if role == "init":
        from hrgid.ops.bootstrap import main as init_main

        init_main()
    elif role == "api":
        from hrgid.ops.serve import main as api_main

        api_main()
    elif role == "worker":
        from hrgid.ops.worker import main as worker_main

        worker_main()
    elif role == "bot":
        if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
            idle("не задан TELEGRAM_BOT_TOKEN (токен выдаёт @BotFather)")
        from hrgid.bot.app import main as bot_main

        bot_main()
    elif importlib.util.find_spec("hrgid.mcp") is None:
        idle("модуль hrgid.mcp отсутствует (MCP-сервер — этап 7)")
    else:
        # В контейнере нет stdin: MCP слушает HTTP, токен — в Authorization запроса.
        port = os.environ.get("HRGID_MCP_PORT", "8765")
        sys.argv = ["hrgid.mcp", "--http", "--host", "0.0.0.0", "--port", port]  # noqa: S104
        runpy.run_module("hrgid.mcp", run_name="__main__")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit(f"использование: python -m hrgid.ops.launch {'|'.join(ROLES)}")
    run(args[0])


if __name__ == "__main__":
    main()
