"""Tests for the H-TURN MECHANISM shadow. SHADOW ONLY -- no order path.

What these pin, in the order the deliverable asks for them:

  1. the BLOCKING mutation gate FAILS when an order-capable module is
     importable, and passes only because the barrier makes it un-importable;
  2. inventory updates on EVERY PARTIAL FILL, and a partial-fill sequence
     reconciles share-for-share;
  3. the complement is NEVER priced above the fee-safe target;
  4. nothing crosses -- the complement always rests strictly below the ask;
  5. an ABSENT complement quote yields UNEVALUABLE, never a default.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.hturn_shadow import mechanism as M


@pytest.fixture(autouse=True)
def barrier():
    M.install_mutation_barrier()
    yield
    M.install_mutation_barrier()


# ------------------------------------------------------- 1. the mutation gate
def test_gate_passes_only_under_the_barrier():
    assert M.mutation_incapable_gate()["pass"] is True
    assert M.assert_mutation_incapable()["pass"] is True


def test_gate_fails_when_an_order_capable_module_is_importable():
    """The gate is a GATE, not a formality: with the barrier removed the
    order-capable modules of this repository import, and it must FAIL."""
    removed = M.remove_mutation_barrier()
    assert removed >= 1
    try:
        g = M.mutation_incapable_gate()
        assert g["pass"] is False, (
            "src.l5_pmus_transport / pmus_q1_executor / pmus_micro_executor "
            "are on disk in this repository, so without the barrier the gate "
            "MUST fail")
        assert g["importable_order_capable_modules"]
        with pytest.raises(PermissionError):
            M.assert_mutation_incapable()
    finally:
        M.install_mutation_barrier()


def test_barrier_refuses_the_import_itself():
    for mod in M.FORBIDDEN_MODULES:
        with pytest.raises(ImportError):
            __import__(mod)


def test_replay_refuses_to_run_without_the_barrier():
    from analysis.l5.hturn_shadow import shadow as S
    M.remove_mutation_barrier()
    try:
        with pytest.raises(PermissionError):
            S.replay_window("slug", [], {})
    finally:
        M.install_mutation_barrier()


# --------------------------------------------- 2. inventory on EVERY partial
def test_inventory_books_every_partial_and_reconciles_share_for_share():
    inv = M.Inventory()
    inv.apply_fill("Up", 100, "0.50")
    assert inv.residual_signed == D(100)
    # one complement order hit in four partials
    seen = []
    for q in (10, 25, 40, 25):
        d = inv.apply_fill("Down", q, "0.45")
        seen.append(D(d["residual_after"]))
    assert inv.fills == 5, "every partial is a fill of its own size"
    assert seen == [D(90), D(65), D(25), D(0)], (
        "residual must step down on EVERY partial, not only on completion")
    assert inv.matched_qty == D(100)
    assert inv.residual_signed == D(0)
    assert inv.avg_pair_cost() == D("0.950000")


def test_partial_then_stop_leaves_the_right_residual():
    """An implementation that only booked COMPLETE fills would report 0."""
    inv = M.Inventory()
    inv.apply_fill("Down", 120, "0.40")
    inv.apply_fill("Up", 40, "0.55")
    assert inv.residual_signed == D(-80)
    assert inv.matched_qty == D(40)
    assert inv.unmatched_avg_cost("Down") == D("0.400000")


def test_resting_complement_partial_fills_keep_the_remainder_resting():
    r = M.RestingComplement("Down", D("0.44"), D("120"), 0.0)
    assert r.take(D("50"), 1.0) == D("50")
    assert r.qty_open == D("70")
    assert r.take(D("200"), 2.0) == D("70"), "never fills more than is open"
    assert r.qty_open == D(0)
    assert len(r.partial_fills) == 2


def test_fractional_partials_reconcile_exactly():
    inv = M.Inventory()
    inv.apply_fill("Up", "0.33", "0.50")
    inv.apply_fill("Up", "0.33", "0.50")
    inv.apply_fill("Down", "0.66", "0.45")
    assert inv.residual_signed == D(0)
    assert inv.matched_qty == D("0.66")


def test_apply_fill_refuses_a_non_positive_fill():
    with pytest.raises(ValueError):
        M.Inventory().apply_fill("Up", 0, "0.5")


# ------------------------------------- 3. never above the fee-safe target
def test_complement_is_never_priced_above_the_fee_safe_target():
    ceil_ = M.pair_ceiling()
    for basis in ("0.10", "0.30", "0.50", "0.62", "0.70", "0.80", "0.90"):
        for ask in ("0.05", "0.20", "0.35", "0.50", "0.75", "0.99"):
            r = M.fee_safe_complement_price(D(basis), D(ask))
            if r["status"] != M.EVALUABLE:
                continue
            q = D(r["price"])
            assert D(basis) + q + M.FEE_ALLOWANCE_PER_SHARE <= ceil_, (
                "fee-inclusive pair cost above the frozen ceiling")
            assert D(r["pair_cost_if_filled"]) <= ceil_


def test_near_par_tolerance_is_not_reachable_from_the_default():
    r = M.fee_safe_complement_price(D("0.55"), D("0.90"))
    assert D(r["pair_cost_if_filled"]) <= M.pair_ceiling() == D("0.96")
    assert M.PAIR_CEILING_NEAR_PAR_IS_LOCKED is True


def test_no_fee_safe_price_is_unevaluable_not_a_widened_quote():
    """A surplus basis that leaves no room must REFUSE, never drift to par."""
    r = M.fee_safe_complement_price(D("0.95"), D("0.50"))
    assert r["status"] == M.U_NOT_POSTABLE
    assert "price" not in r


# -------------------------------------------------------- 4. nothing crosses
def test_complement_rests_strictly_below_the_ask():
    for ask in ("0.05", "0.11", "0.40", "0.99"):
        r = M.fee_safe_complement_price(D("0.10"), D(ask))
        if r["status"] != M.EVALUABLE:
            continue
        assert D(r["price"]) < D(ask)
        assert M.crosses(r["price"], ask) is False
        assert r["crosses"] is False and r["post_only"] is True


def test_crosses_detects_a_cross():
    assert M.crosses("0.50", "0.50") is True
    assert M.crosses("0.51", "0.50") is True
    assert M.crosses("0.49", "0.50") is False


def test_quote_repr_uses_the_one_proven_serializer():
    """No duplicate serializer. A NO/Down order's WIRE value is 1 - price,
    while the price stays in its own outcome's terms internally."""
    q = M.quote_repr(slug="cpc-btc-updown-15m-2026-08-21-0000z",
                     outcome="Down", price=D("0.44"), qty=D("5"))
    assert q["SHADOW_ONLY_NEVER_SENT"] is True
    assert q["outcome_terms_price"] == "0.44"
    body = q["wire"]
    assert body["outcomeSide"] == "OUTCOME_SIDE_NO"
    assert body["participateDontInitiate"] is True
    assert D(str(body["price"]["value"])) == D("0.56")
    up = M.quote_repr(slug="cpc-btc-updown-15m-2026-08-21-0000z",
                      outcome="Up", price=D("0.44"), qty=D("5"))
    assert D(str(up["wire"]["price"]["value"])) == D("0.44")


# ------------------------------- 5. an absent complement quote = UNEVALUABLE
def test_absent_complement_quote_is_unevaluable_not_a_default():
    r = M.fee_safe_complement_price(D("0.50"), None)
    assert r["status"] == M.U_NO_ASK
    assert "price" not in r, "no default price may be invented"
    assert r["status"] in M.UNEVALUABLE_LABELS


def test_missing_complement_book_is_unevaluable_in_the_replay():
    from analysis.l5.hturn_shadow import shadow as S
    fills = [{"t": 100.0, "outcome": "Up", "side": "BUY", "price": 0.50,
              "size": 10, "usdcSize": 5.0, "_i": 0},
             {"t": 160.0, "outcome": "Up", "side": "BUY", "price": 0.51,
              "size": 10, "usdcSize": 5.1, "_i": 1},
             {"t": 200.0, "outcome": "Down", "side": "BUY", "price": 0.49,
              "size": 10, "usdcSize": 4.9, "_i": 2}]
    w = S.replay_window("slug", fills, {})      # no books at all
    assert w["turns"], "the L5 turn is still identified"
    assert w["turns"][0]["status"] == M.U_NO_BOOK
    assert w["turns"][0]["shadow_turn_t"] is None
    assert w["n_shadow_complement_fills"] == 0


def test_stale_complement_book_is_unevaluable_not_carried():
    from analysis.l5.historical_l2.bbo_offset import Book, MAX_CARRY_S
    from analysis.l5.hturn_shadow import shadow as S
    up, down = Book(), Book()
    for bk in (up, down):
        bk.add(0.0, _row(0.50, 0.51))
        bk.sort()
    t0 = MAX_CARRY_S + 500.0
    fills = [{"t": t0, "outcome": "Up", "side": "BUY", "price": 0.50,
              "size": 10, "usdcSize": 5.0, "_i": 0},
             {"t": t0 + 5, "outcome": "Up", "side": "BUY", "price": 0.50,
              "size": 10, "usdcSize": 5.0, "_i": 1},
             {"t": t0 + 9, "outcome": "Down", "side": "BUY", "price": 0.49,
              "size": 10, "usdcSize": 4.9, "_i": 2}]
    w = S.replay_window("slug", fills, {"Up": up, "Down": down})
    assert w["turns"][0]["status"] == M.U_STALE


# ------------------------------------------------- the mechanism, end to end
def _row(bid, ask, bsz=500.0, asz=500.0):
    return {"bid_prices": [bid], "bid_sizes": [bsz],
            "ask_prices": [ask], "ask_sizes": [asz],
            "best_bid": bid, "best_ask": ask, "seconds_to_close": 300.0}


def test_the_turn_is_produced_by_a_fill_not_a_prediction():
    """A resting fee-safe complement, hit when the complement's ask comes
    DOWN to it. The turn is an arrival; no classifier is consulted."""
    from analysis.l5.historical_l2.bbo_offset import Book
    from analysis.l5.hturn_shadow import shadow as S
    up, down = Book(), Book()
    for t in range(0, 40):
        up.add(float(t), _row(0.49, 0.50))
        # the complement's ask sits at 0.60 and only crosses the resting
        # fee-safe bid at t=20
        down.add(float(t), _row(0.40, 0.60 if t < 20 else 0.40, asz=7.0))
    up.sort(); down.sort()
    fills = [{"t": 2.0, "outcome": "Up", "side": "BUY", "price": 0.49,
              "size": 10, "usdcSize": 4.9, "_i": 0},
             {"t": 4.0, "outcome": "Up", "side": "BUY", "price": 0.49,
              "size": 10, "usdcSize": 4.9, "_i": 1},
             {"t": 30.0, "outcome": "Down", "side": "BUY", "price": 0.49,
              "size": 20, "usdcSize": 9.8, "_i": 2}]
    w = S.replay_window("slug", fills, {"Up": up, "Down": down},
                        fee_allowance=D("0"))
    assert w["n_shadow_complement_fills"] >= 1
    f0 = w["shadow_fills"][0]
    assert f0["t"] == 20.0, "the fill is the ask arriving, not a decision"
    assert f0["is_partial"] is True and D(f0["qty"]) == D("7")
    turn = w["turns"][0]
    assert turn["status"] == M.EVALUABLE
    assert turn["shadow_turn_t"] == 20.0
    assert turn["turn_timing_delta_s"] == -10.0
    assert D(turn["shadow_pair_cost_if_hit"]) <= M.pair_ceiling()
    # inventory moved on the partial, and the remainder kept resting
    assert D(w["shadow_fills"][0]["residual_after"]) == D("13")
    assert sum(1 for x in w["shadow_fills"] if x["is_partial"]) >= 1


def test_shadow_does_not_take_l5s_complete_fills():
    from analysis.l5.historical_l2.bbo_offset import Book
    from analysis.l5.hturn_shadow import shadow as S
    up, down = Book(), Book()
    for t in range(0, 20):
        up.add(float(t), _row(0.49, 0.50))
        down.add(float(t), _row(0.40, 0.95))      # never crosses our bid
    up.sort(); down.sort()
    fills = [{"t": 1.0, "outcome": "Up", "side": "BUY", "price": 0.49,
              "size": 10, "usdcSize": 4.9, "_i": 0},
             {"t": 2.0, "outcome": "Up", "side": "BUY", "price": 0.49,
              "size": 10, "usdcSize": 4.9, "_i": 1},
             {"t": 3.0, "outcome": "Down", "side": "BUY", "price": 0.60,
              "size": 20, "usdcSize": 12.0, "_i": 2}]
    w = S.replay_window("slug", fills, {"Up": up, "Down": down})
    assert D(w["l5_final"]["residual_signed"]) == D(0), "L5 completed"
    assert D(w["shadow_final"]["residual_signed"]) == D(20), (
        "the shadow must NOT inherit L5's turn -- it finds one or it does not")
    assert w["turns"][0]["shadow_turn_t"] is None


def test_no_rebate_or_reward_term_exists():
    src = open(os.path.join(os.path.dirname(M.__file__),
                            "mechanism.py")).read().lower()
    assert "rebate" in src, "the module must SAY rebates are zero"
    for bad in ("rebate_credit", "reward_credit", "+ rebate", "rebate_bps"):
        assert bad not in src


# ------------------------------------------- the published artifact's invariants
def test_published_mechanism_json_invariants():
    path = os.path.join(os.path.dirname(M.__file__), "MECHANISM.json")
    if not os.path.exists(path):
        pytest.skip("MECHANISM.json not generated in this checkout")
    d = json.load(open(path))
    assert d["label"] == "SHADOW"
    assert d["gates"]["mutation_incapable"]["pass"] is True
    assert d["economics"]["rebates_credited_usd"] == 0
    assert d["economics"]["rewards_credited_usd"] == 0
    assert d["economics"]["near_par_tolerance"] == "LOCKED"
    assert d["regime"]["rows_in_R1_R2_R3"] == 0
    for name, arm in d["arms"].items():
        s = arm["pooled_is_R4_only"]
        assert s["unevaluable_fraction"] is not None
        if name.startswith("diagnostic"):
            assert arm["authorized"] is False
            continue
        assert arm["pair_ceiling"] == "0.96"
        assert s["pair_cost"]["shadow_if_hit_frac_le_ceiling"] == 1.0
        for t in (t for w in arm["windows"] for t in w["turns"]):
            if t["shadow_pair_cost_if_hit"] is not None:
                assert D(t["shadow_pair_cost_if_hit"]) <= D("0.96")
