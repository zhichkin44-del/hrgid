"""Checks one rules item for compliance with the Labor Code (the check_policy_clause tool).

The same approach as in the panel (hrgid.docs.tkcheck): Labor Code norms from search, model
assessment, the article only
from the norms found; a "risk" without an article and a wording is not accepted.
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
        if res.verdict == "unclear":  # one retry, as in the panel
            res = grade_item(item, norms, self.llm.complete(req).text)
        return {
            "verdict": res.verdict,
            "label": res.label,
            "explanation": res.explanation,
            "article": res.article,
            "suggestion": res.suggestion,
            "norms": res.norms,
        }
