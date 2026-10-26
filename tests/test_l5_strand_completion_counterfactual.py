"""The stranded-leg counterfactual, and the assumptions it refuses to make.

The regression at the bottom uses REAL captured values from the only
venue-confirmed full strand with book data: `cpc-btc-updown-15m-2026-10-25-
1945z`, 1.56 UP at own cost 0.44, acquisition $0.6864, residual settled
worthless for a realized −$0.6864.
"""
from __future__ import annotations

import ast
from decimal import Decimal as D

from src import l5_strand_completion_counterfactual as CF

HELD_PX, HELD_QTY = D("0.44"), D("1.56")


def _row(*, bids, taker_px=None, insufficient=False, resid="1.56",
         queue="500", t=1.0):
    return {
        "label": CF.LABEL, "slug": "w", "t": t,
        "residual": resid, "held_side": "UP", "order_resting": False,
        "book": {"row": {"t": t, "bids": bids, "asks": [],
                         "n_bids": len(bids), "n_asks": 0}},
        "reachability": {
            "taker_walk": {"vwap_own_px": taker_px,
                           "insufficient_displayed_depth": insufficient},
            "maker_quote": {"own_px": "0.9900",
                            "queue_ahead_multiple_of_need": queue},
            "maker_quote_behind_touch": {"own_px": "0.9700"},
        },
    }


def _one(row, won=False):
    return CF.analyse([row], held_px=HELD_PX, held_qty=HELD_QTY,
                      held_side_wins=won)


# ------------------------------------------- what is NOT counted available

def test_a_maker_touch_is_never_counted_as_available():
    """The venue publishes no per-order queue position, so a touch is not a
    fill. A maker-inclusive counterfactual can always 'fix' a strand by
    assuming the fill it never got."""
    out = _one(_row(bids=[], taker_px=None, insufficient=True, queue="20563"))
    assert out["instants_taker_available"] == 0
    assert out["instants_sell_available"] == 0
    assert out["best_taker_settled_pnl_usd"] is None
    assert out["best_sell_settled_pnl_usd"] is None
    # reported, but it bought us nothing
    assert out["rows"][0]["maker_join_own_px"] == "0.9900"
    assert out["rows"][0]["maker_queue_ahead_multiple_of_need"] == "20563"


def test_insufficient_displayed_depth_is_not_an_opportunity():
    out = _one(_row(bids=[["0.45", "100"]], taker_px="0.55",
                    insufficient=True))
    assert out["instants_taker_available"] == 0
    assert out["rows"][0]["taker_pair_own_cost"] is None


def test_no_bid_means_the_residual_cannot_be_sold():
    out = _one(_row(bids=[], taker_px=None, insufficient=True))
    assert out["instants_with_no_bid_on_held_side"] == 1
    assert out["rows"][0]["sell_available"] is False


# ------------------------------------------------------------- economics

def test_a_pair_above_one_dollar_is_reported_as_a_certain_loss():
    """Completing into a pair over $1 converts an uncertain loss into a sure
    one. It must be visible, not hidden behind the word 'completion'."""
    out = _one(_row(bids=[["0.30", "100"]], taker_px="0.70"))
    r = out["rows"][0]
    assert D(r["taker_pair_own_cost"]) == D("1.14")
    assert D(r["taker_settled_pnl"]) < 0
    assert out["instants_with_sub_1_pair_by_taker"] == 0


def test_a_sub_one_dollar_pair_is_locked_on_both_outcomes():
    out_lose = _one(_row(bids=[["0.50", "100"]], taker_px="0.50"), won=False)
    out_win = _one(_row(bids=[["0.50", "100"]], taker_px="0.50"), won=True)
    assert out_lose["instants_with_sub_1_pair_by_taker"] == 1
    # the completed pair's P&L does not depend on which side settles
    assert (out_lose["best_taker_settled_pnl_usd"]
            == out_win["best_taker_settled_pnl_usd"])


def test_selling_at_the_bid_equals_completing_at_one_minus_the_bid():
    """Holding UP and buying DOWN at own cost (1-b) is synthetically the
    same trade as selling UP at b. If these ever diverge beyond fees the
    model has a sign error somewhere."""
    out = _one(_row(bids=[["0.45", "100"]], taker_px="0.55"))
    r = out["rows"][0]
    assert D(r["taker_settled_pnl"]) == D(r["sell_settled_pnl"])


def test_the_sell_walks_across_rungs_and_reports_an_incomplete_exit():
    out = _one(_row(bids=[["0.45", "1.0"], ["0.40", "0.2"]]))
    r = out["rows"][0]
    assert r["sell_available"] is True
    assert r["sell_complete"] is False         # 1.2 of 1.56 needed
    assert D(r["sell_vwap"]) < D("0.45")       # worse than the touch


def test_fees_are_taker_rate_rounded_up_and_never_net_a_rebate():
    assert CF.taker_fee(D("1.56"), D("0.55")) == D("0.03")
    assert CF.taker_fee(D("1.56"), D("0.01")) > 0      # never free
    # an impossible price is charged at the worst a share can cost
    assert CF.taker_fee(D("1"), D("5")) == CF.taker_fee(D("1"), D("1"))


def test_doing_nothing_is_scored_from_the_settled_outcome():
    lose = _one(_row(bids=[["0.45", "100"]], taker_px="0.55"), won=False)
    win = _one(_row(bids=[["0.45", "100"]], taker_px="0.55"), won=True)
    assert D(lose["actual_settled_pnl_usd"]) == D("-0.6864")
    assert D(win["actual_settled_pnl_usd"]) == D("1.56") - D("0.6864")


def test_it_is_structurally_read_only():
    src = ast.parse(open("src/l5_strand_completion_counterfactual.py").read())
    calls = {getattr(n.func, "attr", getattr(n.func, "id", ""))
             for n in ast.walk(src) if isinstance(n, ast.Call)}
    for verb in ("urlopen", "Request", "post", "submit", "cancel",
                 "rmtree", "unlink", "remove"):
        assert verb not in calls, verb


# ------------------------------------------------- real captured regression

def test_the_1945z_numbers_reproduce_from_real_captured_rows():
    """Three real samples from `cpc-btc-updown-15m-2026-10-25-1945z`:
    the window-close state (no bid at all, pair 1.43) and the 0.99 instant
    that a causal exit rule would have taken."""
    rows = [
        # 120 s in: pair exactly $1.00, so exiting costs only the fee
        _row(bids=[["0.44", "36"]], taker_px="0.56", t=1792957620.1),
        # 204 s in: the best executable instant in the window
        _row(bids=[["0.45", "38"]], taker_px="0.55", t=1792957704.2),
        # window close: bids [] , n_bids 0 -- nothing to cross, nothing to sell
        _row(bids=[], taker_px=None, insufficient=True, t=1792958397.2),
    ]
    out = CF.analyse(rows, held_px=HELD_PX, held_qty=HELD_QTY,
                     held_side_wins=False)
    assert out["samples"] == 3
    assert out["instants_with_no_bid_on_held_side"] == 1
    assert out["instants_taker_available"] == 2
    assert D(out["actual_settled_pnl_usd"]) == D("-0.6864")
    # the measured best, and it is a near-breakeven exit rather than a profit
    assert D(out["best_taker_settled_pnl_usd"]) == D("-0.0144")
    assert D(out["improvement_vs_actual_usd"]) == D("0.6720")
    # the deployed 0.96 pair cap would have refused BOTH executable instants
    assert all(D(r["taker_pair_own_cost"]) > D("0.96")
               for r in out["rows"] if r["taker_pair_own_cost"])
