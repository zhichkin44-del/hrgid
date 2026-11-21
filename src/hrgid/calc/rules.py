"""Loads rules/labor_rules.yaml, the single source of numbers and article references."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Any

import yaml

from hrgid.calc.common import ROOT


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    data = yaml.safe_load((ROOT / "rules" / "labor_rules.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def rule(code: str) -> dict[str, Any]:
    r = _load()["rules"][code]
    assert isinstance(r, dict)
    return r


def param(code: str, name: str) -> Any:
    return rule(code)["params"][name]


def dparam(code: str, name: str) -> Decimal:
    return Decimal(str(param(code, name)))


def table(code: str, name: str) -> dict[int, Decimal]:
    return {int(k): Decimal(str(v)) for k, v in rule(code)[name].items()}
