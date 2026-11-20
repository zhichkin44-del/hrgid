from __future__ import annotations

import json
from typing import Any

import pytest

from hrgid.mcp.gateway import TOOLS, McpDenied, McpInputError

from .conftest import A, B, Rig


def call(rig: Rig, who: str, tool: str, /, **args: Any) -> dict[str, Any]:
    return rig.gw.call(rig.tok[who], tool, args)


def test_four_tools_all_declared() -> None:
    assert [t.name for t in TOOLS] == ["search_law", "search_policy", "calc", "check_policy_clause"]


def test_calc_schema_has_branch_per_calculation() -> None:
    schema = next(t for t in TOOLS if t.name == "calc").schema
    kinds = [b["properties"]["kind"]["const"] for b in schema["oneOf"]]
    assert len(kinds) == 9 and schema["properties"]["kind"]["enum"] == kinds
    vp = next(b for b in schema["oneOf"] if b["properties"]["kind"]["const"] == "vacation_pay")
    assert "earnings" in vp["properties"]["args"]["properties"]


def test_search_law_returns_article_ref(rig: Rig) -> None:
    res = call(rig, A, "search_law", query="сколько дней основной отпуск")["results"]
    assert res and res[0]["ref"] == "ТК РФ, ст. 115" and "28 календарных" in res[0]["text"]


def test_search_law_never_returns_company_rules(rig: Rig) -> None:
    res = call(rig, A, "search_law", query="отпуск сотрудника правила")["results"]
    assert all(r["ref"].startswith("ТК РФ") for r in res)


def test_search_policy_returns_own_rules_only(rig: Rig) -> None:
    res = call(rig, A, "search_policy", query="отпуск сотрудника")["results"]
    assert [r["ref"] for r in res] == ["Правила alpha, п. 4.1"]


def test_isolation_foreign_token_gets_zero_foreign_data(rig: Rig) -> None:
    q = "правила компании премия коэффициент отпуск сотрудника секрет"
    for tenant, other_secret in ((A, "beta"), (B, "alpha")):
        out = json.dumps(
            [
                call(rig, tenant, "search_policy", query=q),
                call(rig, tenant, "search_law", query=q),
            ],
            ensure_ascii=False,
        )
        assert other_secret not in out.lower()
    assert call(rig, A, "search_policy", query="премия особому коэффициенту")["results"] == []


def test_company_cannot_be_chosen_by_argument(rig: Rig) -> None:
    res = call(rig, A, "search_policy", query="премия коэффициент", company="beta", tenant_id=B)
    assert res["results"] == []


def test_pii_masked_in_policy_answers(rig: Rig) -> None:
    out = json.dumps(call(rig, A, "search_policy", query="отпуск сотрудника"), ensure_ascii=False)
    assert "Иванов" not in out and "123-45-67" not in out
    assert "[ФИО_1]" in out and "[ТЕЛЕФОН_1]" in out


CALCS: list[tuple[str, dict[str, Any]]] = [
    ("vacation_pay", {"earnings": 600000, "vacation_start": "2026-12-01", "vacation_days": 14}),
    ("leave_compensation", {"earnings": 600000, "dismissal_date": "2026-12-10", "unused_days": 10}),
    ("accrued_vacation", {"hire_date": "2025-01-10", "on_date": "2026-11-20"}),
    (
        "sick_pay",
        {
            "earnings_by_year": {"2024": 800000, "2025": 900000},
            "start": "2026-11-10",
            "end": "2026-11-19",
            "insurance_years": 6,
        },
    ),
    (
        "overtime_pay",
        {
            "monthly_salary": 100000,
            "norm_hours": 168,
            "days": [{"date": "2026-11-10", "hours": 3}],
        },
    ),
    (
        "weekend_work_pay",
        {"day": "2026-11-07", "hours": 8, "monthly_salary": 100000, "norm_hours": 168},
    ),
    ("last_working_day", {"application_date": "2026-11-02"}),
    ("payment_deadline", {"kind": "vacation", "ref_date": "2026-12-14"}),
    ("probation_end", {"start": "2026-11-02", "months": 3}),
]


@pytest.mark.parametrize(("kind", "args"), CALCS, ids=[k for k, _ in CALCS])
def test_calc_all_nine(rig: Rig, kind: str, args: dict[str, Any]) -> None:
    out = call(rig, A, "calc", kind=kind, args=args)
    assert out["code"].startswith("R") and out["article"] and out["value"]
    assert out["formula"] and out["substituted"]


def test_calc_errors_are_readable(rig: Rig) -> None:
    with pytest.raises(McpInputError, match="Не хватает данных"):
        call(rig, A, "calc", kind="vacation_pay", args={"earnings": 1})
    with pytest.raises(McpInputError, match="Неизвестный расчёт"):
        call(rig, A, "calc", kind="nope", args={})


def test_check_clause_risk_goes_masked_to_model(rig: Rig) -> None:
    out = call(
        rig, A, "check_policy_clause",
        text="Для Иванова Ивана Ивановича испытание шесть месяцев.",
    )  # fmt: skip
    assert out["verdict"] == "risk" and out["article"] == "ст. 70" and out["suggestion"]
    sent = rig.llm.requests[0].messages[0].content
    assert "Иванов" not in sent and "[ФИО_1]" in sent


def test_check_clause_ok(rig: Rig) -> None:
    out = call(rig, A, "check_policy_clause", text="Испытание три месяца.")
    assert out["verdict"] == "ok" and out["label"] == "соответствует"


def test_check_clause_without_model_is_a_clear_error(rig: Rig) -> None:
    rig.gw.clause = None
    with pytest.raises(McpInputError, match="не настроена"):
        call(rig, A, "check_policy_clause", text="x")


def test_revoked_token_denied_for_every_tool(rig: Rig) -> None:
    rig.tokens.revoke(A, rig.tokens.list(A)[0]["id"], "admin")
    for tool, args in (
        ("search_law", {"query": "отпуск"}),
        ("search_policy", {"query": "отпуск"}),
        ("calc", {"kind": "probation_end", "args": {"start": "2026-11-02", "months": 3}}),
        ("check_policy_clause", {"text": "x"}),
    ):
        with pytest.raises(McpDenied):
            rig.gw.call(rig.tok[A], tool, args)


def test_garbage_token_denied_for_every_tool(rig: Rig) -> None:
    for tool in ("search_law", "search_policy", "calc", "check_policy_clause"):
        with pytest.raises(McpDenied):
            rig.gw.call("hrg_чужой", tool, {})
        with pytest.raises(McpDenied):
            rig.gw.call(None, tool, {})
