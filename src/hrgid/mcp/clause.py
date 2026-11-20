"""Проверка одного пункта правил на соответствие ТК (инструмент check_policy_clause).

Тот же приём, что в панели (hrgid.docs.tkcheck): нормы ТК из поиска, оценка модели, статья только
из найденных норм; «риск» без статьи и формулировки не принимается.
"""

from __future__ import annotations

from typing import Any, Protocol

from hrgid.docs.parse import Item
from hrgid.docs.tkcheck import SYSTEM, NormSource, grade_item, user_prompt
from hrgid.llm.base import LLMClient, LLMRequest, Message


class ClauseChecker(Protocol):
    def check(self, text: str) -> dict[str, Any]: ...


class LlmClauseChecker:
    def __init__(self, llm: LLMClient, norms: NormSource, k: int = 4) -> None:
        self.llm, self.norms, self.k = llm, norms, k

    def check(self, text: str) -> dict[str, Any]:
        item = Item("1", text)
        norms = self.norms.find(text, self.k)
        req = LLMRequest(
            system=SYSTEM,
            messages=(Message("user", user_prompt(item, norms)),),
            tier="main",
            max_tokens=700,
            purpose="tkcheck",
        )
        res = grade_item(item, norms, self.llm.complete(req).text)
        if res.verdict == "unclear":  # одна повторная попытка, как в панели
            res = grade_item(item, norms, self.llm.complete(req).text)
        return {
            "verdict": res.verdict,
            "label": res.label,
            "explanation": res.explanation,
            "article": res.article,
            "suggestion": res.suggestion,
            "norms": res.norms,
        }
