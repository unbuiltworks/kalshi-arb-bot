"""L5_CHAIN_OBSERVER is read-only research infrastructure and is NOT promoted.

The point of the module is not that it is fast. It is that it is CHECKABLE: a
faster feed that disagrees about what happened is worse than a slow one that
does not, and the failure is silent because a missed fill looks exactly like
L5 not trading.
"""
import ast
import pathlib

import pytest

from src.l5_chain_observer import observer as O

PKG = pathlib.Path(O.__file__).parent


def test_not_promoted():
    """Promotion is a human decision taken after cross-validation, never a
    side effect of the code working."""
    assert O.PROMOTED is False


def test_no_order_path_anywhere_in_the_package():
    """Structural, over the AST, so a future edit adding one fails CI rather
    than being caught by review."""
    banned = {"place_order", "submit_order", "cancel_order", "send_transaction",
              "eth_sendRawTransaction", "sign_transaction", "sign", "post_order"}
    for f in PKG.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = (getattr(fn, "attr", None) or getattr(fn, "id", None))
                assert name not in banned, f"{f.name}: calls {name}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "eth_sendRawTransaction" not in node.value, f.name


def test_it_never_imports_an_executor_or_fidelity():
    """It must not be able to reach execution, and it must not touch FIDELITY
    v0.2, which is frozen and accumulating."""
    for f in PKG.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                assert "executor" not in m, f"{f.name} imports {m}"
                assert "l5_fidelity" not in m, f"{f.name} imports {m}"
                assert "pmus" not in m, f"{f.name} imports {m}"


def test_an_rpc_failure_raises_and_is_never_an_empty_answer():
    """THE BUG THIS PINS. The first version returned None on error, so a
    refused eth_getLogs became zero fills -- it reported 0 against the 480 the
    feed had, and called itself ok. "Could not ask" and "the answer is
    nothing" must never be the same value."""
    with pytest.raises(O.RpcError):
        O.rpc("eth_getLogs", [{"bogus": True}],
              url="https://127.0.0.1:1/never")


def test_chain_fills_checked_reports_failure_rather_than_emptiness(monkeypatch):
    def boom(*a, **k):
        raise O.RpcError("refused")
    monkeypatch.setattr(O, "chain_fills", boom)
    ok, fills = O.chain_fills_checked(1, 2)
    assert ok is False and fills == {}


def test_feed_only_blocks_promotion_and_chain_only_does_not():
    """chain_only is the whole point -- the chain is ~12s ahead of
    publication. feed_only means the chain MISSED a fill, and that is the set
    that must be empty."""
    w = (0.0, 10.0)
    ahead = O.cross_validate({"a": {}, "b": {}}, {"b": {"ts": 1}}, window=w)
    assert ahead["chain_only"] == ["a"] and ahead["feed_only"] == []
    assert ahead["promotion_blocker"] is None

    missed = O.cross_validate({"b": {}}, {"b": {"ts": 1}, "c": {"ts": 2}},
                              window=w)
    assert missed["feed_only"] == ["c"]
    assert "MISSED" in missed["promotion_blocker"]


def test_an_unwindowed_comparison_is_never_a_promotion_test():
    """Without a settled window a feed-only row may simply be inside the
    publication lag, so the comparison cannot license promotion."""
    r = O.cross_validate({"a": {}}, {"a": {"ts": 1}})
    assert r["windowed"] is False
    assert "INDICATIVE" in r["promotion_blocker"]
