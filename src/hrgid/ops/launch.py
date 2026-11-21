"""Container entry point: `python -m hrgid.ops.launch <role>`.

Roles: init, api, worker, bot, mcp. A bot without a token and an MCP without a module (stage 7 not
yet merged) do not crash;
they honestly write the reason and wait: `docker compose up` stays green, and the reason is visible
in the log.
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
        # The container has no stdin: MCP listens over HTTP, the token is in the Authorization
        # header of the request.
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
