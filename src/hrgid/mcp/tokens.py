"""Токены компании для ИИ-агентов: выдаёт и отзывает администратор (П9), хранятся хэшем."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hrgid.bot.store import DocStore

K_TOKEN = "mcp_token"
PREFIX = "hrg_"


def token_hash(token: str) -> str:
    """Токен случайный и длинный, поэтому достаточно SHA-256 без соли (поиск по хэшу)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class McpTokens:
    def __init__(self, store: DocStore, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))

    def issue(self, company: str, label: str, by: str) -> tuple[str, dict[str, Any]]:
        """Новый токен. Открытый вид возвращается один раз и нигде не хранится."""
        token = PREFIX + secrets.token_urlsafe(32)
        n = self.store.next_seq(company, K_TOKEN)
        rec: dict[str, Any] = {
            "id": f"t{n}",
            "hash": token_hash(token),
            "hint": token[-4:],
            "label": label.strip() or "ИИ-агент",
            "created_at": self.clock().isoformat(),
            "created_by": by,
            "revoked_at": None,
            "revoked_by": None,
            "last_used_at": None,
        }
        self.store.put(K_TOKEN, company, rec["id"], rec)
        return token, self.public(rec)

    @staticmethod
    def public(rec: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in rec.items() if k != "hash"}

    def list(self, company: str) -> list[dict[str, Any]]:
        recs = [self.public(r) for r in self.store.all(K_TOKEN, company)]
        return sorted(recs, key=lambda r: int(str(r["id"])[1:]), reverse=True)

    def revoke(self, company: str, token_id: str, by: str) -> bool:
        rec = self.store.get(K_TOKEN, company, token_id)
        if rec is None or rec["revoked_at"] is not None:
            return False
        rec["revoked_at"] = self.clock().isoformat()
        rec["revoked_by"] = by
        self.store.put(K_TOKEN, company, token_id, rec)
        return True

    def verify(self, token: str | None) -> str | None:
        """Компания токена или None (неизвестный, чужой формат, отозванный)."""
        if not token or not token.startswith(PREFIX):
            return None
        found = self.store.find(K_TOKEN, "hash", token_hash(token))
        if len(found) != 1:
            return None
        company, rec = found[0]
        if rec["revoked_at"] is not None:
            return None
        rec["last_used_at"] = self.clock().isoformat()
        self.store.put(K_TOKEN, company, rec["id"], rec)
        return company
