"""Answer flow on LangGraph (§5): route -> search -> answer with ids -> citation check -> refusal.

Graph: route -> (cache) -> search -> answer -> verify -> (one rebuild) -> refusal.
Calculations go down a separate branch via tool calling; Python computes them (hrgid.calc).
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from hrgid.calc.common import CalcError, CalcResult
from hrgid.flow import prompts
from hrgid.flow.cache import CACHEABLE_ROUTES, AnswerCache, cache_key
from hrgid.flow.mask import Masker
from hrgid.flow.tools import TOOLS, format_result, run_tool
from hrgid.flow.trace import MemoryTraceSink, TraceRecord, TraceSink
from hrgid.llm.base import (
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    Tier,
    UsageMeter,
)
from hrgid.llm.resilient import USER_MESSAGE
from hrgid.search import SearchHit
from hrgid.textnorm import normalize

Status = Literal[
    "answered", "no_answer", "unverified", "off_topic", "to_hr", "calc", "clarify", "error"
]

NO_ANSWER_TEXT = "В законе и правилах компании ответа нет. Могу передать вопрос HR."
UNVERIFIED_TEXT = "Не удалось подтвердить ответ источниками. Могу передать вопрос HR."
OFF_TOPIC_TEXT = (
    "Я отвечаю только на вопросы о работе и кадрах: по Трудовому кодексу и правилам компании."
)
TO_HR_TEXT = "Этот вопрос лучше решить с HR. Передать им обращение?"
_LEAK = re.compile(r"sk-ant|api[_ -]?key|ANTHROPIC_|системн\w+ промпт|system prompt", re.I)
_MIN_QUOTE = 12


class Searcher(Protocol):
    def search(self, question: str, tenants: Sequence[str]) -> list[SearchHit]: ...


@dataclass(frozen=True)
class Source:
    fragment_id: int
    ref: str
    article: str
    quote: str
    tenant_id: str


@dataclass
class FlowAnswer:
    status: Status
    text: str
    route: str = ""
    sources: list[Source] = field(default_factory=list)
    cached: bool = False
    unknown_labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AskRequest:
    question: str
    company_id: str
    group: str = ""
    history: tuple[str, ...] = ()
    profile: dict[str, str] | None = None


@dataclass(frozen=True)
class FlowConfig:
    no_answer_threshold: float | None = None  # ADR-0006: chosen on the tuning part
    max_chars: int = 900
    rebuilds: int = 1
    history_limit: int = 6
    docs_version: str = "tk-2026"
    calc_rounds: int = 4


class _State(TypedDict, total=False):
    question: str
    history: list[str]
    company_id: str
    group: str
    profile: dict[str, str] | None
    route: str
    hits: list[SearchHit]
    candidate: dict[str, Any]
    attempt: int
    feedback: str
    result: FlowAnswer
    cache_hit: bool


def _parse_json(text: str) -> dict[str, Any] | None:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        data = json.loads(text[a : b + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_route(text: str) -> str:
    data = _parse_json(text)
    cand = str(data.get("route", "")) if data else text
    cand = cand.strip().lower()
    for r in prompts.ROUTES:
        if cand == r or (not data and r in cand):
            return r
    return "to_hr"


def quote_in_fragment(quote: str, fragment_text: str) -> bool:
    """The quote (after normalization) is present in the fragment; an ellipsis separates the pieces
    in order."""
    frag = normalize(fragment_text)
    pieces = [normalize(p) for p in re.split(r"…|\.\.\.", quote)]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < _MIN_QUOTE:
        return False
    pos = 0
    for p in pieces:
        i = frag.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


class Pipeline:
    def __init__(
        self,
        cheap_llm: LLMClient,
        main_llm: LLMClient,
        searcher: Searcher,
        masker: Masker | None = None,
        cache: AnswerCache | None = None,
        traces: TraceSink | None = None,
        config: FlowConfig | None = None,
    ) -> None:
        self.cheap_llm = cheap_llm
        self.main_llm = main_llm
        self.searcher = searcher
        self.masker = masker or Masker()
        self.cache = cache
        self.traces = traces or MemoryTraceSink()
        self.cfg = config or FlowConfig()
        self._tl = threading.local()
        self.graph = self._build()

    @property
    def _meter(self) -> UsageMeter:
        m: UsageMeter | None = getattr(self._tl, "meter", None)
        if m is None:
            m = self._tl.meter = UsageMeter()
        return m

    # --- model calls ---
    def _call(self, tier: Tier, req: LLMRequest) -> LLMResponse:
        client = self.cheap_llm if tier == "cheap" else self.main_llm
        resp = client.complete(req)
        self._meter.add(resp)
        return resp

    def _llm(self, tier: Tier, system: str, user: str, purpose: str, max_tokens: int = 1024) -> str:
        req = LLMRequest(system, (Message("user", user),), tier, (), max_tokens, purpose)
        return self._call(tier, req).text

    # --- nodes ---
    def _n_route(self, s: _State) -> _State:
        text = self._llm(
            "cheap", prompts.ROUTE_SYSTEM, prompts.route_user(s["question"], s["history"]),
            "route", 50,
        )  # fmt: skip
        return {"route": parse_route(text)}

    def _after_route(self, s: _State) -> str:
        r = s["route"]
        if r in ("law", "rules"):
            return "cache"
        return {"calc": "calc", "off_topic": "off_topic"}.get(r, "to_hr")

    def _n_cache(self, s: _State) -> _State:
        if self.cache is None or s.get("profile"):
            return {}
        hit = self.cache.get(
            cache_key(s["company_id"], s["group"], self.cfg.docs_version, s["question"])
        )
        if hit is not None:
            return {"result": hit, "cache_hit": True}
        return {}

    def _after_cache(self, s: _State) -> str:
        return "end" if s.get("cache_hit") else "search"

    def _n_search(self, s: _State) -> _State:
        hits = self.searcher.search(s["question"], [s["company_id"]])
        th = self.cfg.no_answer_threshold
        if not hits or (th is not None and hits[0].score < th):
            return {"hits": hits, "result": FlowAnswer("no_answer", NO_ANSWER_TEXT, s["route"])}
        return {"hits": hits}

    def _after_search(self, s: _State) -> str:
        return "end" if "result" in s else "answer"

    def _n_answer(self, s: _State) -> _State:
        user = prompts.answer_user(s["question"], s["history"], s["hits"], s.get("feedback", ""))
        text = self._llm("main", prompts.ANSWER_SYSTEM, user, "answer", 1500)
        data = _parse_json(text)
        if data and data.get("no_answer") is True:
            return {"result": FlowAnswer("no_answer", NO_ANSWER_TEXT, s["route"])}
        return {"candidate": data or {}}

    def _after_answer(self, s: _State) -> str:
        return "end" if "result" in s else "verify"

    def _check(self, cand: dict[str, Any], hits: list[SearchHit]) -> tuple[list[Source], str]:
        """Returns (sources, refusal reason). An empty reason means the check passed."""
        text = cand.get("answer")
        claims = cand.get("claims")
        if not isinstance(text, str) or not text.strip():
            return [], "нет текста ответа в JSON"
        if len(text) > self.cfg.max_chars:
            return [], f"ответ длиннее {self.cfg.max_chars} знаков"
        if _LEAK.search(text):
            return [], "ответ содержит служебные данные"
        if not isinstance(claims, list) or not claims:
            return [], "нет утверждений с цитатами"
        by_id = {h.id: h for h in hits}
        sources: list[Source] = []
        for c in claims:
            if not isinstance(c, dict):
                return [], "неверный формат утверждения"
            fid, quote, claim = (
                c.get("fragment_id"),
                str(c.get("quote", "")),
                str(c.get("claim", "")),
            )
            hit = by_id.get(fid) if isinstance(fid, int) else None
            if hit is None:
                return [], f"фрагмента {fid} нет среди найденных"
            if not quote_in_fragment(quote, hit.text):
                return [], f"цитаты нет во фрагменте {fid}: «{quote[:60]}»"
            verdict = self._llm(
                "cheap", prompts.SUPPORT_SYSTEM, prompts.support_user(claim, quote), "support", 5
            )
            if not verdict.strip().lower().startswith("да"):
                return [], f"цитата не подтверждает утверждение «{claim[:60]}»"
            sources.append(Source(hit.id, hit.ref, hit.article, quote, hit.tenant_id))
        return sources, ""

    def _n_verify(self, s: _State) -> _State:
        sources, why = self._check(s["candidate"], s["hits"])
        if not why:
            text = str(s["candidate"]["answer"]).strip()
            return {"result": FlowAnswer("answered", text, s["route"], sources)}
        attempt = s.get("attempt", 0) + 1
        if attempt > self.cfg.rebuilds:
            return {"result": FlowAnswer("unverified", UNVERIFIED_TEXT, s["route"])}
        return {"attempt": attempt, "feedback": why}

    def _after_verify(self, s: _State) -> str:
        return "end" if "result" in s else "answer"

    def _n_calc(self, s: _State) -> _State:
        profile = ""
        prof = s.get("profile")
        if prof:
            profile = "Профиль сотрудника: " + "; ".join(f"{k}: {v}" for k, v in prof.items())
        msgs: list[Message] = [
            Message("user", f"{prompts.history_block(s['history'])}{profile}\n{s['question']}")
        ]
        results: list[CalcResult] = []
        text = ""
        for _ in range(self.cfg.calc_rounds):
            req = LLMRequest(prompts.CALC_SYSTEM, tuple(msgs), "main", TOOLS, 800, "calc")
            resp = self._call("main", req)
            text = resp.text
            if not resp.tool_calls:
                break
            msgs.append(Message("assistant", resp.text, resp.tool_calls))
            for call in resp.tool_calls:
                try:
                    r = run_tool(call.name, call.args)
                    results.append(r)
                    out = format_result(r)
                except CalcError as e:
                    out = f"Ошибка: {e}"
                msgs.append(Message("tool", out, tool_call_id=call.id, tool_name=call.name))
        if not results:
            return {"result": FlowAnswer("clarify", text or TO_HR_TEXT, "calc")}
        block = "\n\n".join(format_result(r) for r in results)
        srcs = [Source(0, r.article, r.article, r.formula, "law") for r in results]
        return {"result": FlowAnswer("calc", f"{text}\n\n{block}".strip(), "calc", srcs)}

    def _n_off_topic(self, s: _State) -> _State:
        return {"result": FlowAnswer("off_topic", OFF_TOPIC_TEXT, "off_topic")}

    def _n_to_hr(self, s: _State) -> _State:
        return {"result": FlowAnswer("to_hr", TO_HR_TEXT, "to_hr")}

    def _build(self) -> Any:
        g: Any = StateGraph(_State)
        g.add_node("route", self._n_route)
        g.add_node("cache", self._n_cache)
        g.add_node("search", self._n_search)
        g.add_node("answer", self._n_answer)
        g.add_node("verify", self._n_verify)
        g.add_node("calc", self._n_calc)
        g.add_node("off_topic", self._n_off_topic)
        g.add_node("to_hr", self._n_to_hr)
        g.set_entry_point("route")
        routes = {"cache": "cache", "calc": "calc", "off_topic": "off_topic", "to_hr": "to_hr"}
        g.add_conditional_edges("route", self._after_route, routes)
        g.add_conditional_edges("cache", self._after_cache, {"end": END, "search": "search"})
        g.add_conditional_edges("search", self._after_search, {"end": END, "answer": "answer"})
        g.add_conditional_edges("answer", self._after_answer, {"end": END, "verify": "verify"})
        g.add_conditional_edges("verify", self._after_verify, {"end": END, "answer": "answer"})
        for n in ("calc", "off_topic", "to_hr"):
            g.add_edge(n, END)
        return g.compile()

    # --- input ---
    def ask(self, req: AskRequest) -> FlowAnswer:
        t0 = time.perf_counter()
        self._tl.meter = UsageMeter()
        masked, mapping = self.masker.mask(req.question)
        history = [self.masker.mask(q, mapping)[0] for q in req.history[-self.cfg.history_limit :]]
        state: _State = {
            "question": masked, "history": history, "company_id": req.company_id,
            "group": req.group, "profile": req.profile, "attempt": 0, "feedback": "",
        }  # fmt: skip
        route = ""
        hits: list[SearchHit] = []
        cache_hit = False
        try:
            final: _State = self.graph.invoke(state)
            ans = final["result"]
            route = final.get("route", "")
            hits = final.get("hits", [])
            cache_hit = bool(final.get("cache_hit"))
            if (
                self.cache is not None and not req.profile and not cache_hit
                and ans.status in ("answered", "no_answer") and route in CACHEABLE_ROUTES
            ):  # fmt: skip
                self.cache.put(
                    cache_key(req.company_id, req.group, self.cfg.docs_version, masked),
                    FlowAnswer(ans.status, ans.text, ans.route, list(ans.sources)),
                )
        except LLMError:
            ans = FlowAnswer("error", USER_MESSAGE, route)
        out_text, unknown = mapping.unmask(ans.text)
        out = FlowAnswer(
            ans.status, out_text, ans.route or route, list(ans.sources), cache_hit, unknown
        )
        m = self._meter
        self.traces.write(
            TraceRecord(
                tenant_id=req.company_id, route=out.route, status=out.status,
                masked_question=masked, fragment_ids=tuple(h.id for h in hits),
                models=tuple(m.models), input_tokens=m.input_tokens,
                output_tokens=m.output_tokens, cost_rub=m.cost,
                latency_ms=int((time.perf_counter() - t0) * 1000), cache_hit=cache_hit,
                llm_calls=m.calls,
            )
        )  # fmt: skip
        return out
