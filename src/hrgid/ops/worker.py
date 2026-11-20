"""Фоновый работник: хранение данных (§9) и сигнал жизни. Доставку outbox ведёт бот (hrgid.bot)."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("hrgid.ops.worker")

HEARTBEAT_KEY = "hrgid:worker:heartbeat"


def purge_old_traces(conn: Any, days: int) -> int:
    """Удаляет трассы старше срока хранения (по умолчанию 1 год, §9). Вернёт число строк."""
    cur = conn.execute("DELETE FROM traces WHERE ts < now() - make_interval(days => %s)", (days,))
    return int(cur.rowcount)


def beat(redis_client: Any, now: float, ttl: int = 120) -> None:
    redis_client.set(HEARTBEAT_KEY, str(int(now)), ex=ttl)


def run_once(conn: Any, redis_client: Any, days: int, now: float) -> int:
    beat(redis_client, now)
    return purge_old_traces(conn, days)


def run_forever(
    step: Callable[[], object],
    every: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    rounds: int | None = None,
) -> None:
    done = 0
    while rounds is None or done < rounds:
        try:
            step()
        except Exception:
            log.exception("шаг работника")
        done += 1
        sleep(every)


def main() -> None:
    import redis

    from hrgid.db import database_url
    from hrgid.flow.trace import TRACE_SCHEMA
    from hrgid.ops.bootstrap import wait_for_db

    logging.basicConfig(level=logging.INFO)
    conn = wait_for_db(database_url())
    conn.execute(TRACE_SCHEMA)
    r = redis.Redis.from_url(os.environ.get("REDIS_URL") or "redis://redis:6379/0")
    days = int(os.environ.get("HRGID_RETENTION_DAYS") or "365")

    def step() -> int:
        n = run_once(conn, r, days, time.time())
        if n:
            log.info("удалено трасс старше %d дн.: %d", days, n)
        return n

    run_forever(step)
