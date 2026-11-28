"""Each test is a way an entry gate could be handed a confident wrong
rate, and the assertion that it is not.

The real BTC15m settlement file is used where it exists, so the headline
numbers in the module docstring are checked rather than asserted in
prose.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src import l5_measured_entry_rates as M

SETTLED = os.path.join("data", "l5", "ops",
                       "btc15m_settled_20261012_20261105.jsonl")


def _row(matched_qty="0", matched_pnl="0", residual_qty="0",
         residual_cost="0", settlement_pnl="0"):
    return {"matched_qty": matched_qty, "matched_pnl": matched_pnl,
            "residual_qty": residual_qty, "residual_cost": residual_cost,
            "settlement_pnl": settlement_pnl}


# ------------------------------------- RULE 1: the placeholder is excluded

def test_a_residual_at_exactly_one_dollar_is_the_placeholder():
    """Nobody buys a binary at $1 -- the best case is the dollar back."""
    r = _row(residual_qty="5", residual_cost="5.00", settlement_pnl="-5.00")
    assert M.residual_unit_cost(r) == D("1")
    assert M.classify(r) == M.UPPER_BOUND


def test_a_residual_below_one_dollar_is_a_measured_fill():
    r = _row(residual_qty="5", residual_cost="2.175", settlement_pnl="-2.175")
    assert M.classify(r) == M.RESIDUAL


def test_placeholders_are_excluded_from_the_estimate_not_averaged_in():
    """THE PROPERTY THAT MATTERS. On the real file this is the difference
    between -$0.34 and -$0.02 a market."""
    rows = ([_row(matched_qty="2", matched_pnl="0.20",
                  settlement_pnl="0.20")] * 20
            + [_row(residual_qty="5", residual_cost="5.00",
                    settlement_pnl="-5.00")] * 20)
    est = M.estimate(rows)
    assert est["n_excluded_upper_bound"] == 20
    assert est["n_authenticated"] == 20
    assert est["n_residual"] == 0
    # the -$5 placeholders cannot drag the measured mean
    assert D(est["mean_realized_usd"]) == D("0.20")


def test_paired_and_residual_are_classified_and_counted_separately():
    rows = ([_row(matched_qty="2", matched_pnl="0.17",
                  settlement_pnl="0.17")] * 12
            + [_row(residual_qty="3", residual_cost="1.20",
                    settlement_pnl="-0.14")] * 12)
    est = M.estimate(rows, min_n=20)
    assert (est["n_paired"], est["n_residual"]) == (12, 12)
    assert D(est["pair_margin_usd"]) == D("0.17")
    assert D(est["residual_loss_usd"]) == D("-0.14")


def test_a_market_with_no_position_is_neither_a_pair_nor_a_loss():
    est = M.estimate([_row()] * 30)
    assert est["n_no_position"] == 30
    assert est["n_authenticated"] == 0
    assert est["ok"] is False


def test_an_unreadable_pnl_is_counted_not_treated_as_zero():
    rows = [_row(matched_qty="2", matched_pnl="0.1",
                 settlement_pnl="0.1")] * 20
    rows.append(_row(residual_qty="2", residual_cost="0.5",
                     settlement_pnl="not-a-number"))
    est = M.estimate(rows)
    assert est["n_unreadable_pnl"] == 1
    assert est["n_authenticated"] == 20


# --------------------------------- RULE 2: a small sample cannot open

def test_below_min_n_the_estimate_refuses_rather_than_guessing():
    est = M.estimate([_row(matched_qty="1", matched_pnl="5",
                           settlement_pnl="5")] * 4)
    assert est["ok"] is False
    assert est["reason"] == "TOO_FEW_AUTHENTICATED_MARKETS"
    assert est["ev_per_market_usd"] is None
    assert est["ev_lower_usd"] is None


def test_a_refused_estimate_is_never_a_permission():
    est = M.estimate([_row(matched_qty="1", matched_pnl="5",
                           settlement_pnl="5")] * 4)
    v = M.entry_is_justified(est)
    assert v["justified"] is False
    assert v["why"] == "TOO_FEW_AUTHENTICATED_MARKETS"


def test_shrinkage_pulls_an_extreme_pair_rate_toward_no_information():
    """20 of 20 paired is 1.00 observed; the shrunk rate must be below
    it, so a streak cannot be read as a certainty."""
    est = M.estimate([_row(matched_qty="1", matched_pnl="0.1",
                           settlement_pnl="0.1")] * 20)
    assert D(est["p_pair_observed"]) == D("1")
    assert D(est["p_pair"]) < D("1")
    assert D(est["p_pair"]) == (D(20) + D(10) * D("0.5")) / (D(20) + D(10))


# ------------------------------- RULE 3: the gate reads the lower bound

def test_a_positive_mean_on_a_noisy_sample_is_not_an_edge():
    """THE CASE THIS PROGRAM KEEPS HITTING. Alternating +1/-1 with a
    slight positive tilt has a positive mean and a lower bound below
    zero, and must NOT justify an entry."""
    rows = []
    for i in range(30):
        pnl = "1.05" if i % 2 == 0 else "-1.00"
        if i % 2 == 0:
            rows.append(_row(matched_qty="1", matched_pnl=pnl,
                             settlement_pnl=pnl))
        else:
            rows.append(_row(residual_qty="1", residual_cost="0.5",
                             settlement_pnl=pnl))
    est = M.estimate(rows)
    assert D(est["ev_per_market_usd"]) > 0
    assert D(est["ev_lower_usd"]) < 0
    assert M.entry_is_justified(est)["justified"] is False


def test_a_consistent_edge_does_justify_an_entry():
    """The gate is not simply always-refuse: a tight positive sample
    passes, which is what makes the refusals above meaningful."""
    rows = [_row(matched_qty="1", matched_pnl="0.20",
                 settlement_pnl="0.20")] * 30
    est = M.estimate(rows)
    assert D(est["se_usd"]) == 0
    assert M.entry_is_justified(est)["justified"] is True


def test_the_min_ev_floor_is_honoured():
    rows = [_row(matched_qty="1", matched_pnl="0.20",
                 settlement_pnl="0.20")] * 30
    est = M.estimate(rows)
    assert M.entry_is_justified(est, min_ev_usd="0.50")["justified"] is False
    assert M.entry_is_justified(est, min_ev_usd="0.50")["why"] == \
        "EV_LOWER_NOT_ABOVE_MIN"


def test_an_unreadable_floor_refuses():
    rows = [_row(matched_qty="1", matched_pnl="0.2",
                 settlement_pnl="0.2")] * 30
    v = M.entry_is_justified(M.estimate(rows), min_ev_usd="abc")
    assert v == {"justified": False, "why": "MIN_EV_UNREADABLE"}


def test_a_larger_z_is_strictly_more_conservative():
    rows = []
    for i in range(40):
        pnl = "0.30" if i % 2 == 0 else "-0.10"
        rows.append(_row(matched_qty="1", matched_pnl=pnl,
                         settlement_pnl=pnl))
    lo1 = D(M.estimate(rows, z=D("1"))["ev_lower_usd"])
    lo2 = D(M.estimate(rows, z=D("2"))["ev_lower_usd"])
    assert lo2 < lo1


# ---------------------------------------- the real file, as evidence

@pytest.mark.skipif(not os.path.exists(SETTLED), reason="tape absent")
def test_the_real_btc15m_file_reproduces_the_documented_numbers():
    """The module docstring claims n=148 authenticated, 20 excluded,
    p_pair 0.3716 observed and about -$0.02 a market. If the tape or the
    classifier changes, this fails rather than the prose going stale."""
    rows = []
    with open(SETTLED) as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("retired") and str(r.get("market", "")).startswith(
                    "cpc-btc-updown-15m"):
                rows.append(r)
    est = M.estimate(rows)
    assert est["ok"] is True
    assert est["n_excluded_upper_bound"] == 20
    assert est["n_authenticated"] == 148
    assert est["n_paired"] == 55
    assert est["n_residual"] == 93
    assert D(est["p_pair_observed"]).quantize(D("0.0001")) == D("0.3716")
    # marginally negative on measured prices -- NOT the -0.3392 the
    # placeholders imply
    ev = D(est["ev_per_market_usd"])
    assert D("-0.10") < ev < D("0")
    # and it does not justify an entry
    assert M.entry_is_justified(est)["justified"] is False


def test_the_module_is_pure():
    import inspect
    src = inspect.getsource(M)
    for forbidden in ("urllib", "requests", "open(", "os.environ",
                      "getenv", "store.", "save("):
        assert forbidden not in src, forbidden
