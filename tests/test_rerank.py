from hrgid.rerank import CANDIDATES, CrossEncoderReranker


def test_two_candidates_declared() -> None:
    assert len(CANDIDATES) == 2


def test_reranker_empty_input_needs_no_model() -> None:
    r = CrossEncoderReranker.__new__(CrossEncoderReranker)
    assert r.score("q", []) == []
