"""MIRROR_RAW's resting-order lifecycle and the three economic bounds.

Two properties carry the whole experiment's honesty, and both are pinned here:
a trade that happened BEFORE an order became actionable can never bear on it,
and the three bounds never share an inventory.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_authenticated_evidence as E
from src import pmus_interaction_classifier as clf
from src import pmus_mirror_lifecycle as M


def _order(**kw):
    o = {"market": "m", "side": "UP", "price": "0.22",
         "actionable_from": 100.0, "window_end": 1000.0,
         "queue_ahead": None, "copyable": True, "l5_relation": "open"}
    o.update(kw)
    return o


def _norm(row):
    """A normalizer with PROVEN semantics, for testing the machinery only.
    The production one refuses to map an unproven vocabulary."""
    out = dict(row)
    out["yes_price"] = row["px"]
    out["size"] = row.get("qty")
    if row.get("taker") in ("yes", "no"):
        out["taker_side"] = row["taker"]
    return out


def test_a_trade_before_the_order_was_actionable_is_ignored():
    """The decisive no-backdating rule. A print that happened while the news
    was still in flight says nothing about an order we had not placed."""
    prints = {"m": [{"t": 50.0, "px": "0.20", "qty": "99", "taker": "no"}]}
    got = M.scan([_order()], prints, normalize=_norm)
    assert got[0]["prints_considered"] == 0
    assert got[0]["klass"] == clf.NO_INTERACTION


def test_a_trade_after_it_counts():
    prints = {"m": [{"t": 150.0, "px": "0.20", "qty": "99", "taker": "no"}]}
    got = M.scan([_order()], prints, normalize=_norm)
    assert got[0]["prints_considered"] == 1
    assert got[0]["klass"] == clf.TRADE_THROUGH_PRICE


def test_the_life_ends_when_the_intent_changes():
    orders = [_order(actionable_from=100.0),
              _order(actionable_from=300.0, side="DOWN", price="0.30")]
    got = M.scan(orders, {"m": []}, normalize=_norm)
    first = [g for g in got if g["actionable_from"] == 100.0][0]
    assert first["life_ends"] == 300.0
    assert first["life_ended_by"] == "intent_changed"


def test_an_unchanged_intent_lives_to_the_window_end():
    orders = [_order(actionable_from=100.0), _order(actionable_from=300.0)]
    got = M.scan(orders, {"m": []}, normalize=_norm)
    assert got[0]["life_ends"] == 1000.0
    assert got[0]["life_ended_by"] == "window_end"


def test_nothing_lives_past_the_window():
    got = M.scan([_order(window_end=400.0)], {"m": []}, normalize=_norm)
    assert got[0]["life_ends"] == 400.0


def test_no_classification_is_ever_a_fill():
    prints = {"m": [{"t": 150.0, "px": "0.20", "qty": "99", "taker": "no"}]}
    got = M.scan([_order()], prints, normalize=_norm)
    assert got[0]["is_fill"] is False
    assert got[0]["fill_evidence_source"] is None


def test_strict_admits_only_the_queue_independent_class():
    assert M.BOUND_CLASSES[M.STRICT] == (clf.TRADE_THROUGH_PRICE,)
    assert clf.TRADE_AT_PRICE not in M.BOUND_CLASSES[M.STRICT]


def test_a_trade_at_price_reaches_plausible_but_not_strict():
    prints = {"m": [{"t": 150.0, "px": "0.22", "qty": "5", "taker": "no"}]}
    c = M.scan([_order()], prints, normalize=_norm)
    assert c[0]["klass"] == clf.TRADE_AT_PRICE
    b = M.bounds(c)
    assert b["bounds"][M.STRICT]["acquisitions"] == 0
    assert b["bounds"][M.PLAUSIBLE]["acquisitions"] == 1
    assert b["bounds"][M.COPYABILITY]["acquisitions"] == 1


def test_the_three_bounds_never_share_inventory():
    prints = {"m": [{"t": 150.0, "px": "0.20", "qty": "99", "taker": "no"},
                    {"t": 350.0, "px": "0.80", "qty": "99", "taker": "yes"}]}
    orders = [_order(actionable_from=100.0),
              _order(actionable_from=300.0, side="DOWN", price="0.30")]
    b = M.bounds(M.scan(orders, prints, normalize=_norm))
    books = [b["bounds"][x] for x in M.BOUNDS]
    # each bound reports its own acquisition count; no total is offered
    assert "total" not in b
    assert len({id(x) for x in books}) == 3
    assert b["bounds"][M.COPYABILITY]["acquisitions"] >= \
        b["bounds"][M.PLAUSIBLE]["acquisitions"] >= \
        b["bounds"][M.STRICT]["acquisitions"]


def test_the_residual_is_never_marked():
    prints = {"m": [{"t": 150.0, "px": "0.20", "qty": "99", "taker": "no"}]}
    b = M.bounds(M.scan([_order()], prints, normalize=_norm))
    for x in M.BOUNDS:
        assert b["bounds"][x]["residual_settlement_pnl"] is None
        assert "UNMARKED" in b["bounds"][x]["residual_note"]


def test_matched_settlement_is_one_dollar_a_pair_and_rebate_is_separate():
    prints = {"m": [{"t": 150.0, "px": "0.20", "qty": "99", "taker": "no"},
                    {"t": 350.0, "px": "0.80", "qty": "99", "taker": "yes"}]}
    orders = [_order(actionable_from=100.0, price="0.40"),
              _order(actionable_from=300.0, side="DOWN", price="0.50")]
    b = M.bounds(M.scan(orders, prints, normalize=_norm))
    p = b["bounds"][M.PLAUSIBLE]
    if p["matched_pairs"]:
        # settlement = pairs * $1.00 - cost, and the rebate is its own line
        assert D(p["settlement_implied_pnl_matched_usd"]) == (
            D(p["matched_pairs"]) - D(p["avg_pair_cost"]) * p["matched_pairs"])
        #: THE POINT IS THE SEPARATION, NOT THE SIGN. The rebate must be
        #: its own line and never folded into settlement P&L. It is now
        #: ZERO because PMUS re-tiered on 2026-11-14 and this account is
        #: Maker Tier 1, which is credited nothing -- and a zero on its
        #: own line is exactly the right reporting of that.
        assert "maker_rebate_accrual_usd" in p
        assert D(p["maker_rebate_accrual_usd"]) == 0
        assert p["taker_fees_usd"] == "0.0000"
        assert p["liquidity_rewards_usd"] == "0.00"


def test_the_production_normalizer_refuses_an_unproven_aggressor():
    """With the real normalizer and the recorder's own buy/sell vocabulary,
    nothing reaches STRICT or PLAUSIBLE -- by design, until proven."""
    prints = {"m": [{"t": 150.0, "px": "0.20", "qty": "99", "dir": "sell"}]}
    c = M.scan([_order()], prints, normalize=E.normalize_print)
    assert c[0]["klass"] == clf.QUEUE_AMBIGUOUS
    b = M.bounds(c)
    assert b["bounds"][M.STRICT]["acquisitions"] == 0
    assert b["bounds"][M.PLAUSIBLE]["acquisitions"] == 0
