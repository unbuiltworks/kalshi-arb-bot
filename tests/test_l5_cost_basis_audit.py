"""The READ-ONLY cost-basis audit for $1/share placeholder legs."""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_cost_basis_audit as A       # noqa: E402


class FakeStore:
    def __init__(self, unknown, positions, prov=None):
        self.state = {"leg_cost_basis_unknown": list(unknown),
                      "positions": dict(positions),
                      "fill_provenance": dict(prov or {})}


class FakeT:
    """Cursor-paginated activities, read-only."""
    def __init__(self, pages, raise_at=None):
        self.pages, self.raise_at, self.calls = pages, raise_at, []

    def request(self, method, path, body=None, base=None):
        assert method == "GET", "the audit must only ever GET"
        self.calls.append(path)
        if self.raise_at is not None and len(self.calls) > self.raise_at:
            raise OSError("venue unreachable")
        i = len(self.calls) - 1
        return self.pages[i] if i < len(self.pages) else {"activities": [],
                                                          "eof": True}


def _fill(market, side, qty, price, fee, coid=None, tid="t1"):
    return {"kind": "filled", "marketSlug": market, "side": side,
            "quantity": qty, "price": price, "commission": fee,
            "clientOrderId": coid, "tradeId": tid}


# ------------------------------------------------------------------ the point
def test_a_manual_leg_basis_is_measured_and_the_overstatement_reported():
    """The $1/share placeholder vs the real fill price."""
    s = FakeStore(["mBTC|DOWN"], {"mBTC|DOWN": "1.27"})
    t = FakeT([{"activities": [
        _fill("mBTC", "DOWN", "1.27", "0.12", "0.01",
              coid="owner-manual-1")], "eof": True}])
    out = A.audit(s, t)
    leg = out["legs"][0]
    assert leg["basis"]["established"] is True
    assert leg["basis"]["actual_cost_usd"] == "0.1524"
    assert leg["placeholder_cost_usd"] == "1.27"
    assert leg["owners"] == [A.OWNER_MANUAL]        # no coid of ours
    assert out["overstatement_usd"] == "1.1176"
    assert out["writes"] == "NONE"


def test_l5_owned_fills_are_distinguished_from_manual_by_our_own_record():
    prov = {"mX": {"entries": [{"coid": "ours1", "l5_owner_verified": True}]}}
    s = FakeStore(["mX|UP"], {"mX|UP": "2"}, prov)
    t = FakeT([{"activities": [
        _fill("mX", "UP", "1", "0.44", "0.01", coid="ours1", tid="a"),
        _fill("mX", "UP", "1", "0.46", "0.01", coid="theirs", tid="b"),
    ], "eof": True}])
    out = A.audit(s, t)
    leg = out["legs"][0]
    assert leg["l5_owned_fills"] == 1
    assert leg["owner_manual_fills"] == 1
    assert leg["basis"]["established"] is True
    assert leg["basis"]["actual_cost_usd"] == "0.90"


def test_a_fill_with_no_client_order_id_is_unknown_owner_not_manual():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    t = FakeT([{"activities": [
        dict(_fill("m", "DOWN", "1", "0.10", "0.00"), clientOrderId=None)],
        "eof": True}])
    out = A.audit(s, t)
    assert out["legs"][0]["unknown_owner_fills"] == 1
    assert A.UNKNOWN_OWNER in out["legs"][0]["owners"]


# --------------------------------------------------------------- fails closed
@pytest.mark.parametrize("missing,reason", [
    ("price", "FILL_WITHOUT_PRICE"),
    ("commission", "FILL_WITHOUT_FEE"),
    ("quantity", "FILL_WITHOUT_QUANTITY"),
])
def test_a_missing_field_is_never_zero(missing, reason):
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    row = _fill("m", "DOWN", "1", "0.10", "0.01")
    row[missing] = None
    row.pop("avgPx", None); row.pop("cumQuantity", None)
    row.pop("commissionNotionalTotalCollected", None)
    t = FakeT([{"activities": [row], "eof": True}])
    out = A.audit(s, t)
    assert out["legs"][0]["basis"]["established"] is False
    assert out["legs"][0]["basis"]["reason"] == reason
    assert out["establishable"] == 0
    assert out["overstatement_usd"] == "0"


def test_a_quantity_mismatch_refuses_rather_than_understating_cost():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "5"})
    t = FakeT([{"activities": [_fill("m", "DOWN", "2", "0.10", "0.01")],
                "eof": True}])
    out = A.audit(s, t)
    b = out["legs"][0]["basis"]
    assert b["established"] is False and b["reason"] == "QUANTITY_MISMATCH"
    assert b["venue_qty"] == "2" and b["ledger_qty"] == "5"


def test_no_fills_found_keeps_the_placeholder():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    out = A.audit(s, FakeT([{"activities": [], "eof": True}]))
    assert out["legs"][0]["basis"]["reason"] == "NO_FILLS_FOUND"
    assert out["unestablished"] == 1


def test_an_incomplete_activity_walk_establishes_nothing():
    """A truncated feed may be missing fills, so no basis is trusted."""
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    pages = [{"activities": [_fill("m", "DOWN", "1", "0.10", "0.01")],
              "nextCursor": "c%d" % i} for i in range(3)]
    out = A.audit(s, FakeT(pages), max_pages=2)
    assert out["activity_walk"]["complete"] is False
    assert out["activity_walk"]["ended"] == "max_pages"
    assert out["legs"][0]["basis"]["reason"].startswith(
        "ACTIVITY_WALK_INCOMPLETE")
    assert out["establishable"] == 0


def test_an_unreachable_venue_mid_walk_is_reported_not_treated_as_eof():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    pages = [{"activities": [], "nextCursor": "c1"}]
    out = A.audit(s, FakeT(pages, raise_at=1))
    assert out["activity_walk"]["ended"].startswith("error:")
    assert out["activity_walk"]["complete"] is False


# ------------------------------------------------------- non-fill rows ignored
def test_settlements_and_corrections_are_not_fills():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    t = FakeT([{"activities": [
        {"kind": "settlement", "marketSlug": "m", "side": "DOWN",
         "quantity": "1", "price": "1.00", "commission": "0"},
        {"kind": "reconciliation_adjustment", "marketSlug": "m",
         "side": "DOWN", "quantity": "1", "price": "0.50", "commission": "0"},
    ], "eof": True}])
    out = A.audit(s, t)
    assert out["legs"][0]["basis"]["reason"] == "NO_FILLS_FOUND"


def test_the_feed_is_walked_once_for_many_markets():
    """ACCOUNT-scoped: walking per market is what drew HTTP 429."""
    s = FakeStore(["a|DOWN", "b|DOWN", "c|UP"],
                  {"a|DOWN": "1", "b|DOWN": "1", "c|UP": "1"})
    t = FakeT([{"activities": [], "eof": True}])
    A.audit(s, t)
    assert len(t.calls) == 1, t.calls


def test_the_audit_writes_nothing_to_the_store():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1.27"})
    before = repr(s.state)
    A.audit(s, FakeT([{"activities": [
        _fill("m", "DOWN", "1.27", "0.12", "0.01")], "eof": True}]))
    assert repr(s.state) == before


# ------------------------------------------------- wiring into the executor
def test_the_runner_hook_is_env_gated_and_runs_after_the_transport_exists():
    """THE BUG THIS PINS. The hook was first placed in __init__ before
    self.transport was assigned, so it could only ever raise AttributeError
    and the audit would silently never run."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "src/l5_pmus_live_runner.py")).read()
    t = src.index("self.transport = T.PmusTransport(")
    a = src.index('os.getenv("L5_COST_BASIS_AUDIT"')
    e = src.index("self.executor = LX.FaithfulExecutor(")
    assert t < a < e, "the audit must run after the transport is built"
    # gated: a normal boot must not spend the feed walk
    assert '"L5_COST_BASIS_AUDIT", ""' in src
    assert '.upper() == "YES"' in src


def test_the_audit_module_calls_no_write_or_order_verb():
    """Mentions in prose are fine; CALLS are not. Checked on the parsed
    tree so a docstring explaining why it cannot correct a settlement does
    not read as it correcting one."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(A))
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    for forbidden in ("save", "submit", "cancel", "correct_settled_market",
                      "reconstruct_cost_basis", "persist", "put"):
        assert forbidden not in called, forbidden
    # the only venue verb it uses is a GET
    assert "request" in called
    assert '"GET"' in inspect.getsource(A.walk_activities)
    # and no assignment anywhere targets a store/state subscript
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                assert not isinstance(tgt, ast.Subscript), ast.dump(tgt)[:80]
