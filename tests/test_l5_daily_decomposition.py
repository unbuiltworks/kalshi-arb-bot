"""The pair split, and the filter that once turned a loss into a profit."""
from __future__ import annotations

from decimal import Decimal as D

import src.l5_daily_decomposition as M


def t(cid="c1", outcome="Up", side="BUY", size="10", usd="4"):
    return {"conditionId": cid, "outcome": outcome, "side": side,
            "size": size, "usdcSize": usd, "slug": "s", "title": "t"}


def r(cid="c1", usd="10"):
    return {"conditionId": cid, "usdcSize": usd}


def test_a_market_with_no_redemption_is_a_total_loss_not_a_missing_row():
    # the September correction, pinned: filtering on redeem>0 deleted these
    out = M.decompose_day([t(usd="4")], [], {})
    assert out["trading_net"] == D("-4")
    assert out["cohorts"]["one_sided"]["total_loss"] == 1


def test_two_sided_and_one_sided_are_told_apart():
    both = [t(outcome="Up"), t(outcome="Down")]
    assert M.classify(M.by_market(both, [])["c1"]) == "two_sided"
    assert M.classify(M.by_market([t()], [])["c1"]) == "one_sided"


def test_a_matched_pair_locks_one_dollar_minus_its_cost():
    # 10 Up at 0.40 and 10 Down at 0.55 -> pair cost 0.95, locked 0.05/pair
    rec = M.by_market([t(outcome="Up", size="10", usd="4"),
                       t(outcome="Down", size="10", usd="5.5")], [])["c1"]
    ps = M.pair_split(rec)
    assert ps["matched_pairs"] == D("10")
    assert ps["matched_cost"] == D("9.5")
    assert ps["matched_locked_pnl"] == D("0.5")
    assert ps["residual_qty"] == D("0")


def test_the_residual_is_the_imbalance_priced_at_its_own_average():
    rec = M.by_market([t(outcome="Up", size="15", usd="6"),      # 0.40
                       t(outcome="Down", size="10", usd="5")], [])["c1"]
    ps = M.pair_split(rec)
    assert ps["matched_pairs"] == D("10")
    assert ps["residual_qty"] == D("5")
    assert ps["residual_side"] == "Up"
    assert ps["residual_cost"] == D("2.0")      # 5 shares at 0.40


def test_a_one_sided_market_is_all_residual_and_locks_nothing():
    ps = M.pair_split(M.by_market([t(size="10", usd="4")], [])["c1"])
    assert ps["matched_pairs"] == D("0")
    assert ps["matched_locked_pnl"] == D("0")
    assert ps["residual_qty"] == D("10")


def test_sells_reduce_the_position_and_are_counted_as_proceeds():
    rec = M.by_market([t(size="10", usd="4"),
                       t(side="SELL", size="4", usd="2")], [])["c1"]
    assert rec["spend"] == D("4")
    assert rec["proceeds"] == D("2")
    assert rec["qty"]["Up"] == D("6")


def test_trading_net_is_redeem_plus_proceeds_minus_spend():
    out = M.decompose_day([t(usd="4")], [r(usd="10")], {})
    assert out["trading_net"] == D("6")


def test_incentives_are_reported_beside_trading_never_inside_it():
    out = M.decompose_day([t(usd="4")], [],
                          {"MAKER_REBATE": [{"usdcSize": "3"}]})
    assert out["trading_net"] == D("-4")            # unchanged by the rebate
    assert out["incentives_total"] == D("3")
    assert out["total_with_incentives"] == D("-1")


def test_residual_settlement_is_whatever_the_matched_leg_did_not_lock():
    # matched locks +0.5; the day lost 4 overall, so direction cost 4.5
    out = M.decompose_day([t(outcome="Up", size="10", usd="4"),
                           t(outcome="Down", size="10", usd="5.5")],
                          [r(usd="5.5")], {})
    assert out["matched_locked_pnl"] == D("0.5")
    assert out["trading_net"] == D("-4")
    assert out["residual_settlement_pnl"] == D("-4.5")
