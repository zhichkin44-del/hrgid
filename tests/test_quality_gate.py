"""The quality gate turns red on a deliberately degraded prompt (stage 8, §16).

Everything runs on a Fake model: we record "good" answers, then change the prompt or the recordings themselves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hrgid.flow import prompts
from hrgid.flow.gate import PENDING_WARNING, GateResult, evaluate_gate, recordings_pending
from hrgid.flow.graph import Pipeline
from hrgid.flow.mask import Masker
from hrgid.llm.base import LLMRequest, LLMResponse
from hrgid.llm.fake import FakeLLM
from hrgid.llm.recordings import RecordingLLM
from test_flow import HITS, Stub, good_answer

ROWS: list[dict[str, Any]] = [
    {"id": "law-01", "kind": "law", "question": "Сколько дней отпуска?", "gold_articles": ["115"]},
    {"id": "na-01", "kind": "no_answer", "question": "Какая завтра погода?", "gold_articles": []},
]
NO_ANSWER = json.dumps({"no_answer": True, "answer": "", "claims": []})


def lenient(summary: dict[str, tuple[int, int]]) -> dict[str, bool]:
    """Thresholds for a tiny set: everything must pass."""
    return {k: ok == n for k, (ok, n) in summary.items()}


def fake(wrong_article: bool = False) -> FakeLLM:
    def handler(r: LLMRequest) -> LLMResponse | str:
        if r.purpose == "route":
            return json.dumps({"route": "law"})
        if r.purpose == "support":
            return "да"
        if "погода" in r.messages[0].content:
            return NO_ANSWER
        return (
            good_answer(fid=2, quote="не позднее чем за три дня")
            if wrong_article
            else good_answer()
        )

    return FakeLLM(handler)


def pipe(directory: Path, *, record: bool, inner: FakeLLM | None = None) -> Pipeline:
    llm = RecordingLLM(inner, "set-v1", directory, record=record, model_name="fake")
    return Pipeline(llm, llm, Stub(HITS), masker=Masker(lambda t: []))


@pytest.fixture()
def recorded(tmp_path: Path) -> Path:
    res = evaluate_gate(pipe(tmp_path, record=True, inner=fake()), ROWS, check=lenient)
    assert res.status == "pass", res
    return tmp_path


@pytest.fixture()
def degraded_prompt() -> Iterator[None]:
    old = prompts.ANSWER_SYSTEM
    prompts.ANSWER_SYSTEM = old + "\nОтвечай как можно короче и без ссылок."
    yield
    prompts.ANSWER_SYSTEM = old


def test_replay_of_unchanged_prompts_is_green(recorded: Path) -> None:
    res = evaluate_gate(pipe(recorded, record=False), ROWS, check=lenient)
    assert res == GateResult("pass", res.message)


def test_gate_turns_red_on_deliberately_degraded_prompt(
    recorded: Path, degraded_prompt: None
) -> None:
    res = evaluate_gate(pipe(recorded, record=False), ROWS, check=lenient)
    assert res.status == "fail" and not res.ok
    assert "нет записи" in res.message


def test_gate_turns_red_on_degraded_recordings(tmp_path: Path) -> None:
    """The recordings are taken from the degraded model: the keys are in place, but the quality is below the threshold."""
    inner = fake(wrong_article=True)
    res = evaluate_gate(pipe(tmp_path, record=True, inner=inner), ROWS, check=lenient)
    assert res.status == "fail" and "law" in res.message


def test_gate_is_skipped_with_warning_while_recordings_pending(tmp_path: Path) -> None:
    res = evaluate_gate(pipe(tmp_path, record=False), ROWS, pending=True)
    assert res.status == "skipped" and res.ok
    assert res.message == PENDING_WARNING and "PENDING" in res.message


def test_recordings_pending_detects_marker(tmp_path: Path) -> None:
    (tmp_path / "tests" / "recordings").mkdir(parents=True)
    assert not recordings_pending(tmp_path)
    (tmp_path / "tests" / "recordings" / "PENDING").write_text("x", encoding="utf-8")
    assert recordings_pending(tmp_path)
