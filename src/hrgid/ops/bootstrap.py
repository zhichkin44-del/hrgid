"""First start (the init service in Compose): DB schema, law index, first company and admin.

Idempotent: a repeated start breaks nothing and does not re-index what already exists.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from hrgid.bot.models import CompanySettings
from hrgid.bot.repo import Repo
from hrgid.ops.config import apply_secrets, secret, state_dir

log = logging.getLogger("hrgid.ops.init")
ROOT = Path(__file__).resolve().parents[3]


def law_dir() -> Path:
    return Path(os.environ.get("HRGID_LAW_DIR") or ROOT / "data" / "raw")


def needs_index(have: int, want: int) -> bool:
    return have != want


def wait_for_db(url: str, timeout: float = 90.0) -> Any:
    """Waits for PostgreSQL to be ready (the healthcheck is already green, but connections can come
    later)."""
    from hrgid.db import connect

    deadline = time.monotonic() + timeout
    while True:
        try:
            return connect(url)
        except Exception as exc:  # noqa: BLE001
            if time.monotonic() > deadline:
                raise
            log.info("БД ещё не готова: %s", exc)
            time.sleep(2)


def ensure_law_files(directory: Path) -> None:
    """The text of the Labor Code is not stored in the repository: it is downloaded on the first
    start (network required)."""
    if any(directory.glob("st_*.html")):
        return
    log.info("закон не найден в %s, загружаю с consultant.ru", directory)
    directory.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HRGID_LAW_DIR": str(directory)}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_tk.py")], check=True, env=env)


def ensure_company(
    repo: Repo, auth_add_user: Any, company_id: str, name: str, admin_email: str, admin_name: str
) -> bool:
    """The first company and its administrator. True means created now; False means it already
    existed."""
    if not company_id or not admin_email:
        return False
    created = False
    if not repo.store.all("settings", company_id):
        repo.save_settings(CompanySettings(company_id=company_id, company_name=name or company_id))
        created = True
    auth_add_user(company_id, admin_email, admin_name or "Администратор", "admin")
    return created


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from hrgid.bot.store import PgDocStore
    from hrgid.db import database_url, init_schema
    from hrgid.embed import E5Embedder
    from hrgid.law.ingest import load_chunks
    from hrgid.panel.deps import build_deps
    from hrgid.search import store_chunks

    state_dir()
    apply_secrets()
    secret("web_invite", "HRGID_WEB_INVITE")
    conn = wait_for_db(database_url())
    init_schema(conn)
    store = PgDocStore(conn)
    store.init_schema()
    log.info("схема БД готова")

    raw = law_dir()
    ensure_law_files(raw)
    chunks, stats = load_chunks(raw)
    row = conn.execute("SELECT count(*) FROM chunks WHERE scope = 'law'").fetchone()
    have = int(row[0]) if row else 0
    if needs_index(have, stats.chunks):
        log.info("индексирую закон: %d фрагментов (было %d)", stats.chunks, have)
        conn.execute("DELETE FROM chunks WHERE scope = 'law'")
        store_chunks(conn, chunks, E5Embedder())
    else:
        log.info("индекс закона актуален: %d фрагментов", have)

    deps = build_deps(store, pepper=os.environ["HRGID_PANEL_PEPPER"])
    made = ensure_company(
        deps.repo,
        deps.auth.add_user,
        os.environ.get("HRGID_COMPANY", ""),
        os.environ.get("HRGID_COMPANY_NAME", ""),
        os.environ.get("HRGID_ADMIN_EMAIL", ""),
        os.environ.get("HRGID_ADMIN_NAME", ""),
    )
    log.info("компания: %s", "создана" if made else "без изменений")
    log.info("init завершён")


if __name__ == "__main__":
    main()
