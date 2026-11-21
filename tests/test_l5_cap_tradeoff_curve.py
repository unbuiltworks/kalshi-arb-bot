"""OWNER 5569709706 #6: the residual-cap tradeoff curve, deterministic."""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_cap_tradeoff_curve as cv


def test_rebate_estimate_uses_the_published_rate_and_bankers_cent_rounding():
    # 5.31 @0.47: 0.0125*5.31*0.47*0.53 = 0.016535 -> 0.02 (venue credited 0.02)
    assert cv.rebate_estimate(D("5.31"), D("0.47")) == D("0.02")
    # 1.38 @0.49: 0.004310 -> 0.00 (below the floor, venue credited 0.00)
    assert cv.rebate_estimate(D("1.38"), D("0.49")) == D("0.00")
    assert cv.rebate_estimate(D("0.11"), D("0.50")) == D("0.00")


def test_replay_applies_the_r4_rule_and_settles_at_the_actual_outcome():
    # open YES 5@0.50 (unmatched 2.50) -> extend YES 5@0.50 (5.00) -> complete NO 5@0.50
    fills = [("t1", "YES", "0.50", "5"), ("t2", "YES", "0.50", "5"), ("t3", "NO", "0.50", "5")]
    un = cv.replay(fills, None, "YES")
    assert un["kept"] == 3 and un["relation_all"] == {"open": 1, "extend": 1, "complete": 1}
    assert un["matched_qty"] == D("5") and un["pair_pnl_usd"] == D("0")     # pair at 1.00
    assert un["peak_unmatched_usd"] == D("5.00")
    assert un["residual_pnl_usd"] == D("2.5")                                  # 5 YES @0.50 paid $1
    capped = cv.replay(fills, D("2.50"), "YES")
    assert capped["kept"] == 2 and capped["refused"] == 1                      # the extend
    assert capped["relation_kept"] == {"open": 1, "complete": 1}
    assert capped["peak_unmatched_usd"] == D("2.50") and capped["residual_pnl_usd"] == D("0")
    lost = cv.replay(fills, None, "NO")
    assert lost["residual_pnl_usd"] == D("-2.5")


def test_a_complement_is_never_refused_even_when_exposure_is_above_the_cap():
    fills = [("t1", "YES", "0.50", "10"), ("t2", "NO", "0.50", "4"), ("t3", "NO", "0.50", "6")]
    # cap 1.00: the open (5.00) is refused; with nothing to complete, the NO
    # fills are opens of their own (2.00, then 5.00) and are refused too
    r = cv.replay(fills, D("1.00"), None)
    assert r["refused"] == 3 and r["kept"] == 0
    # cap 5.00: open kept (5.00 == cap, not above), both complements kept
    # even though exposure sits AT the cap -- they reduce it
    r2 = cv.replay(fills, D("5.00"), None)
    assert r2["kept"] == 3 and r2["final_unmatched_usd"] == D("0")
    assert r2["relation_kept"] == {"open": 1, "complete": 2}


def test_aggregate_reports_fidelity_income_risk_and_the_objective_ratio():
    # prices are YES-terms: a NO fill at yes_price 0.52 costs 0.48 -> pair 0.98
    fills = [("t1", "YES", "0.50", "5"), ("t2", "YES", "0.50", "5"), ("t3", "NO", "0.52", "5")]
    res = [cv.replay(fills, None, "YES"), cv.replay(fills, D("2.50"), "YES")]
    a = cv.aggregate(res, "x")
    assert a["fills"] == 6 and a["kept"] == 5 and a["fidelity_kept_share"] == 0.8333
    assert a["worst_case_peak_unmatched_usd"] == 5.0
    assert D(a["pair_pnl_usd"]) == D("0.20")            # 2 x 5 pairs at 0.98
    assert a["income_per_dollar_of_peak_residual"] is not None
    assert "ESTIMATED" in a["labels"]


def test_window_fills_convert_down_prices_to_yes_terms_and_find_the_unit():
    rows = [
        {"timestamp": "1", "trade_seq": "1", "side": "BUY", "outcome": "Up", "price": "0.40",
         "quantity": "10", "dollars": "4.0", "settlement_outcome": "Down"},
        {"timestamp": "2", "trade_seq": "2", "side": "BUY", "outcome": "Down", "price": "0.55",
         "quantity": "10", "dollars": "5.5", "settlement_outcome": "Down"},
        {"timestamp": "3", "trade_seq": "3", "side": "SELL", "outcome": "Up", "price": "0.9",
         "quantity": "1", "dollars": "0.9", "settlement_outcome": "Down"},
    ]
    fills, resolution, unit = cv.window_fills(rows)
    assert [(s, str(p)) for _t, s, p, _q in fills] == [("YES", "0.40"), ("NO", "0.45")]
    assert resolution == "NO" and unit == D("4.75")


def test_l5_curve_on_the_canonical_history_has_the_headline_shape():
    out = cv.l5_curve(caps=(None, 1.0, 4.0))
    by = {c["cap"]: c for c in out["curve"]}
    assert out["windows"] == 2216
    assert by["uncapped"]["fidelity_kept_share"] == 1.0
    assert by["1.0_units"]["fidelity_kept_share"] < 0.4          # the R4 cap keeps < 40%
    assert by["4.0_units"]["fidelity_kept_share"] > by["1.0_units"]["fidelity_kept_share"]
    # the cap cuts the residual loss far more than the pair income
    assert D(by["1.0_units"]["residual_pnl_usd"]) > D(by["uncapped"]["residual_pnl_usd"])
    assert by["1.0_units"]["worst_case_peak_unmatched_usd"] < by["uncapped"]["worst_case_peak_unmatched_usd"]
    assert by["1.0_units"]["income_per_dollar_of_peak_residual"] > by["4.0_units"]["income_per_dollar_of_peak_residual"]


def test_our_fills_curve_reproduces_r3l_and_checks_the_rebate_estimate():
    out = cv.our_curve(caps=(None, 1.0), r4_path="/nonexistent")
    assert out["markets"] == sorted(cv.R3L_FILLS)
    un = {c["cap"]: c for c in out["curve"]}["uncapped"]
    assert D(un["residual_pnl_usd"]) == D("-6.29")           # wisc-nd, the R3L loser
    assert un["fidelity_kept_share"] == 1.0
    capped = {c["cap"]: c for c in out["curve"]}["1.0_units"]
    assert capped["cap_usd"] == "2.500" and capped["worst_case_peak_unmatched_usd"] <= 2.5
    assert out["rebate_estimate_check"]["r4_fills_venue_credited_usd"] == "0.14"
