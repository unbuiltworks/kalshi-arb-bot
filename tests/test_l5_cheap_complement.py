"""The cheap-complement reconstruction must be sequential and blind to the future.

A lookahead leak here would not fail loudly: it would quietly inflate every
predictive number in the report, which is the one failure mode that makes a
research result worse than having none.
"""
from __future__ import annotations

import ast
from pathlib import Path

from src import l5_cheap_complement as cc

SRC = Path(cc.__file__).read_text(encoding="utf-8")


def _fill(t, outcome, price, qty, tte=600, seq=None):
    return {"timestamp": str(t), "trade_seq": str(seq if seq is not None else t),
            "side": "BUY", "outcome": outcome, "price": str(price),
            "quantity": str(qty), "seconds_to_settlement": str(tte),
            "asset": "BTC", "duration_minutes": "5",
            "window_id": "w1", "market_slug": "m1"}


# a position built UP at rising prices, then a switch
WINDOW = [
    _fill(100, "Up", 0.40, 10),
    _fill(110, "Up", 0.50, 10),
    _fill(120, "Up", 0.60, 10),          # avg 0.50, last 0.60 -> gain +0.10
    _fill(130, "Down", 0.38, 10),        # the switch
    _fill(140, "Down", 0.36, 10),
    _fill(150, "Up", 0.70, 10),          # a second switch
]


# ---------------------------------------------------- the identity is exact
def test_the_pair_identity_is_exact_not_fitted():
    """pair == last_pair - gain_per_unit, to floating point."""
    recs = cc.walk_window(WINDOW)
    assert recs, "precondition: the fixture switches"
    for r in recs:
        assert abs(r["identity_residual"]) < 1e-12, r
        assert abs(r["pair"] - (r["last_pair"] - r["pre"]["gain_per_unit"])) < 1e-12


def test_the_first_switch_prices_the_pair_against_the_running_average():
    r = cc.walk_window(WINDOW)[0]
    assert abs(r["pre"]["avg_cost_held"] - 0.50) < 1e-9
    assert abs(r["pre"]["last_same_side_price"] - 0.60) < 1e-9
    assert abs(r["pre"]["gain_per_unit"] - 0.10) < 1e-9
    assert abs(r["first_opposite_price"] - 0.38) < 1e-9
    assert abs(r["pair"] - 0.88) < 1e-9          # 0.50 + 0.38
    assert r["pair_le_arm"] is True


# ------------------------------------------------------------- NO LOOKAHEAD
def test_pre_features_cannot_see_past_the_switch():
    """THE load-bearing property. Truncating the window immediately after a
    switch must not change that switch's pre-action features by one bit."""
    full = cc.walk_window(WINDOW)
    for k, rec in enumerate(full):
        cut = [f for f in WINDOW if int(f["timestamp"]) <= rec["t"]]
        truncated = cc.walk_window(cut)
        assert len(truncated) >= k + 1
        assert truncated[k]["pre"] == rec["pre"], (
            f"switch {k}: pre-action features changed when future fills were "
            "removed -- that is a lookahead leak")


def test_the_switch_fills_own_price_is_an_outcome_not_a_feature():
    rec = cc.walk_window(WINDOW)[0]
    assert "first_opposite_price" not in rec["pre"]
    assert "pair" not in rec["pre"]
    assert "pair_le_arm" not in rec["pre"]
    # and no pre-feature equals the switch price by construction
    assert rec["first_opposite_price"] not in [
        v for v in rec["pre"].values() if isinstance(v, float)]


def test_a_switch_is_emitted_before_its_own_fill_is_folded_in():
    """Held quantity at the switch must exclude the switching fill."""
    rec = cc.walk_window(WINDOW)[0]
    assert abs(rec["pre"]["held_qty"] - 30.0) < 1e-9      # 3 Up fills, not 4
    assert rec["pre"]["run_length"] == 3


# --------------------------------------------------------------- fail closed
def test_a_window_with_no_switch_yields_nothing():
    assert cc.walk_window([_fill(1, "Up", 0.5, 1), _fill(2, "Up", 0.6, 1)]) == []


def test_sells_are_ignored_and_malformed_fills_do_not_raise():
    rows = list(WINDOW) + [{"side": "SELL", "outcome": "Up", "price": "0.9",
                            "quantity": "5", "timestamp": "160",
                            "trade_seq": "160"},
                           {"side": "BUY", "outcome": "Up", "price": None,
                            "quantity": "1", "timestamp": "170",
                            "trade_seq": "170"}]
    assert len(cc.walk_window(rows)) == len(cc.walk_window(WINDOW))


def test_auc_is_well_defined_and_symmetric():
    assert cc.auc([True, False], [1.0, 0.0]) == 1.0
    assert cc.auc([True, False], [0.0, 1.0]) == 0.0
    assert cc.auc([True, False], [1.0, 1.0]) == 0.5
    assert cc.auc([True, True], [1.0, 0.0]) is None      # no negatives


# ------------------------------------------------- read-only research module
def test_the_module_places_no_orders_and_reads_nothing():
    tree = ast.parse(SRC)
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            roots.add((n.module or "").split(".")[0])
    assert roots <= {"__future__", "bisect", "math", "collections",
                     "typing"}, roots
    names = {getattr(n, "attr", None) or getattr(n, "id", None)
             for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}
    for bad in ("request", "urlopen", "socket", "place_limit_buy", "open",
                "getenv", "environ", "Transport", "submit"):
        assert bad not in names, bad
