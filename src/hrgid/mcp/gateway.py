"""MCP tool gateway: token check -> company -> tool. Read-only.

The token determines the company; its identifier is not accepted in tool arguments.
Answers based on company rules, item checks and calculations pass personal-data masking (§9).
The text of the law is public and is not masked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from hrgid.calc.common import CalcError, CalcResult
from hrgid.flow.mask import Masker
from hrgid.flow.tools import REGISTRY, run_tool
from hrgid.law.parse import LAW_TENANT
from hrgid.mcp.clause import ClauseChecker
from hrgid.mcp.tokens import McpTokens

MAX_QUERY = 1000
MAX_CLAUSE = 4000


class McpDenied(Exception):
    """The token is unknown, belongs to someone else, or has been revoked."""


class McpInputError(ValueError):
    """Invalid tool arguments: the text is understandable to the model agent."""


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]


def calc_schema() -> dict[str, Any]:
    """Input schema for calc: one branch per calculation R1-R9 (kind + args per the calculation
    schema)."""
    branches = [
        {
            "title": f"{kind}: {spec.description}",
            "type": "object",
            "properties": {"kind": {"const": kind}, "args": spec.schema},
            "required": ["kind", "args"],
        }
        for kind, (spec, _) in REGISTRY.items()
    ]
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(REGISTRY)},
            "args": {"type": "object"},
        },
        "required": ["kind", "args"],
        "oneOf": branches,
    }


_QUERY = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "вопрос или слова для поиска"},
        "k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "сколько норм"},
    },
    "required": ["query"],
}

TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        "search_law",
        "Поиск по Трудовому кодексу РФ: возвращает нормы со ссылкой на статью.",
        _QUERY,
    ),
    ToolDef(
        "search_policy",
        "Поиск по действующим правилам вашей компании: пункты со ссылкой на документ.",
        _QUERY,
    ),
    ToolDef(
        "calc",
        "Детерминированные расчёты R1-R9 по ТК РФ (отпускные, больничный, сверхурочные, сроки). "
        "Считает код, не модель; результат с формулой и основанием. kind выбирает расчёт, "
        "args — по его схеме: "
        + "; ".join(f"{k}: {s.description}" for k, (s, _) in REGISTRY.items()),
        calc_schema(),
    ),
    ToolDef(
        "check_policy_clause",
        "Проверить один пункт правил компании на соответствие ТК РФ: «соответствует», "
        "«лучше закона» или «риск нарушения» со статьёй и предложенной формулировкой.",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "текст пункта правил"}},
            "required": ["text"],
        },
    ),
)


def _jsonable(r: CalcResult) -> dict[str, Any]:
    def one(v: Any) -> Any:
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, date):
            return v.isoformat()
        return v

    return {
        "code": r.code,
        "value": one(r.value),
        "details": {k: one(v) for k, v in r.details.items()},
        "formula": r.formula,
        "substituted": r.substituted,
        "article": r.article,
        "notes": list(r.notes),
    }


class McpGateway:
    """searcher is any object with search(question, [companies]) -> list[SearchHit]."""

    def __init__(
        self,
        tokens: McpTokens,
        searcher: Any,
        masker: Masker | None = None,
        clause: ClauseChecker | None = None,
    ) -> None:
        self.tokens = tokens
        self.searcher = searcher
        self.masker = masker or Masker()
        self.clause = clause

    def authenticate(self, token: str | None) -> str:
        company = self.tokens.verify(token)
        if company is None:
            raise McpDenied("Токен недействителен или отозван.")
        return company

    def call(self, token: str | None, name: str, args: dict[str, Any]) -> dict[str, Any]:
        company = self.authenticate(token)
        if name == "search_law":
            return self._search(company, args, law=True)
        if name == "search_policy":
            return self._search(company, args, law=False)
        if name == "calc":
            return self._calc(args)
        if name == "check_policy_clause":
            return self._clause(args)
        raise McpInputError(f"Неизвестный инструмент: {name}")

    # --- masking ---
    def _mask(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.masker.mask(value)[0]
        if isinstance(value, list):
            return [self._mask(v) for v in value]
        if isinstance(value, dict):
            return {k: self._mask(v) for k, v in value.items()}
        return value

    # --- tools ---
    def _search(self, company: str, args: dict[str, Any], *, law: bool) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise McpInputError("Пустой запрос.")
        k = max(1, min(int(args.get("k", 5)), 10))
        owner = LAW_TENANT if law else company
        hits = self.searcher.search(query[:MAX_QUERY], [owner])
        # Second line of defense: search already filters by tenant_id; checked again here.
        rows = [
            {
                "ref": h.ref,
                "article": h.article,
                "title": h.title,
                "text": h.text,
                "edition": h.edition,
                "score": round(h.score, 4),
            }
            for h in hits
            if h.tenant_id == owner
        ][:k]
        return {"results": rows if law else self._mask(rows)}

    def _calc(self, args: dict[str, Any]) -> dict[str, Any]:
        kind = str(args.get("kind", ""))
        raw = args.get("args")
        if kind not in REGISTRY:
            raise McpInputError(f"Неизвестный расчёт: {kind!r}. Доступны: {', '.join(REGISTRY)}.")
        if not isinstance(raw, dict):
            raise McpInputError("args должен быть объектом по схеме расчёта.")
        try:
            out: dict[str, Any] = self._mask(_jsonable(run_tool(kind, raw)))
        except CalcError as e:
            raise McpInputError(str(e)) from e
        return out

    def _clause(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text", "")).strip()
        if not text:
            raise McpInputError("Пустой текст пункта.")
        if self.clause is None:
            raise McpInputError("Проверка пунктов на этом сервере не настроена (нет модели).")
        masked = self.masker.mask(text[:MAX_CLAUSE])[0]  # only the masked text goes to the model
        out: dict[str, Any] = self._mask(self.clause.check(masked))
        return out
