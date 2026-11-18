"""Кэш ответов (§9): только маршруты «закон» и «правила компании», без данных профиля.

Ключ = company_id + группа сотрудника + версия документов + хэш замаскированного вопроса.
Реализация в памяти; Redis подключается тем же интерфейсом.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from hrgid.textnorm import normalize

CACHEABLE_ROUTES = frozenset({"law", "rules"})


def cache_key(company_id: str, group: str, docs_version: str, masked_question: str) -> str:
    q = hashlib.sha256(normalize(masked_question).encode("utf-8")).hexdigest()
    return "\x1f".join((company_id, group, docs_version, q))


class AnswerCache(Protocol):
    def get(self, key: str) -> Any | None: ...

    def put(self, key: str, value: Any) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._d: dict[str, Any] = {}
        self.hits = 0

    def get(self, key: str) -> Any | None:
        v = self._d.get(key)
        if v is not None:
            self.hits += 1
        return v

    def put(self, key: str, value: Any) -> None:
        self._d[key] = value
