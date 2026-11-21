from __future__ import annotations

from fastapi.testclient import TestClient

from hrgid.flow.graph import Pipeline
from hrgid.flow.mask import Masker
from hrgid.flow.trace import MemoryTraceSink
from hrgid.llm.base import LLMRequest
from hrgid.llm.fake import FakeLLM
from hrgid.web import Invite, create_app
from test_flow import HITS, Stub, good_answer


def _client() -> tuple[TestClient, MemoryTraceSink]:
    def cheap(r: LLMRequest) -> str:
        return '{"route": "law"}' if r.purpose == "route" else "да"

    tr = MemoryTraceSink()
    p = Pipeline(
        FakeLLM(cheap), FakeLLM(lambda r: good_answer()), Stub(HITS),
        masker=Masker(lambda t: []), traces=tr,
    )  # fmt: skip
    app = create_app(p, {"tok-a": Invite("A"), "tok-b": Invite("B")})
    return TestClient(app), tr


def test_page_and_chat() -> None:
    c, tr = _client()
    assert c.get("/c/tok-a").status_code == 200
    assert c.get("/c/nope").status_code == 404
    r = c.post("/api/chat", json={"invite": "tok-a", "question": "Сколько дней отпуска?"})
    d = r.json()
    assert r.status_code == 200 and d["status"] == "answered"
    assert d["sources"][0]["ref"] == "ст. 115 ТК РФ"
    assert tr.records[0].tenant_id == "A"


def test_invite_required_and_tenant_from_invite_only() -> None:
    c, tr = _client()
    bad = c.post("/api/chat", json={"invite": "x", "question": "?"})
    assert bad.status_code == 403
    c.post("/api/chat", json={"invite": "tok-b", "question": "вопрос", "tenant_id": "A"})
    assert tr.records[-1].tenant_id == "B"  # a foreign tenant from the request body is ignored


def test_validation() -> None:
    c, _ = _client()
    assert c.post("/api/chat", json={"invite": "tok-a", "question": ""}).status_code == 422
