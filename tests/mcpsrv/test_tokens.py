from __future__ import annotations

from hrgid.mcp.tokens import K_TOKEN

from .conftest import A, B, Rig


def test_issue_returns_plain_once_and_stores_hash_only(rig: Rig) -> None:
    tok = rig.tok[A]
    assert tok.startswith("hrg_") and len(tok) > 40
    stored = rig.store.all(K_TOKEN, A)
    assert len(stored) == 1
    assert tok not in str(stored)  # открытого токена нет нигде
    assert stored[0]["hash"] != tok and len(stored[0]["hash"]) == 64


def test_verify_maps_token_to_its_company(rig: Rig) -> None:
    assert rig.tokens.verify(rig.tok[A]) == A
    assert rig.tokens.verify(rig.tok[B]) == B


def test_unknown_and_malformed_tokens_denied(rig: Rig) -> None:
    for bad in (None, "", "hrg_nope", "x" * 50, rig.tok[A][:-1], rig.tok[A].upper()):
        assert rig.tokens.verify(bad) is None


def test_revoke_denies_at_once_and_only_that_token(rig: Rig) -> None:
    tid = rig.tokens.list(A)[0]["id"]
    assert rig.tokens.revoke(A, tid, "admin@alpha") is True
    assert rig.tokens.verify(rig.tok[A]) is None
    assert rig.tokens.verify(rig.tok[B]) == B
    assert rig.tokens.revoke(A, tid, "admin@alpha") is False  # повторно — нечего отзывать


def test_cannot_revoke_foreign_company_token(rig: Rig) -> None:
    _, rec = rig.tokens.issue(B, "второй", "admin@beta")  # id t2 есть только у beta
    assert rig.tokens.revoke(A, rec["id"], "admin@alpha") is False
    assert rig.tokens.verify(rig.tok[B]) == B


def test_list_hides_hash_and_tracks_last_use(rig: Rig) -> None:
    rig.tokens.verify(rig.tok[A])
    row = rig.tokens.list(A)[0]
    assert "hash" not in row
    assert row["last_used_at"] and row["label"] == "Claude Code"
