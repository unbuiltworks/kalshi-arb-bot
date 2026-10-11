"""Same-market economics must use own-terms prices, keep matched and residual
apart, and never report an unsettled market as zero."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.pmus_manual_audit.sequence_economics import (market_economics,
                                                              run)

YES_FILL = {"trade_id": "T1", "role": "PASSIVE", "shares": "2",
            "wire_px": "0.45", "side": "ORDER_SIDE_BUY",
            "outcomeSide": "OUTCOME_SIDE_YES",
            "intent": "ORDER_INTENT_BUY_LONG", "commission": "0.0000",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"}
NO_FILL = {"trade_id": "T2", "role": "PASSIVE", "shares": "2",
           "wire_px": "0.49", "side": "ORDER_SIDE_SELL",
           "outcomeSide": "OUTCOME_SIDE_NO",
           "intent": "ORDER_INTENT_BUY_SHORT", "commission": "0.0000",
           "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"}


def test_a_NO_fill_is_priced_in_own_terms():
    r = market_economics({"slug": "m", "fills": [NO_FILL]})
    assert r["NO_avg_own_price"] == "0.51", "wire 0.49 is own 0.51"
    assert r["NO_shares"] == "2"
    assert r["YES_shares"] == "0"


def test_a_completed_pair_locks_its_edge_before_resolution():
    r = market_economics({"slug": "m", "fills": [YES_FILL, NO_FILL]})
    assert r["matched_pairs"] == "2"
    assert r["paired_cost_per_pair"] == "0.96"
    assert r["matched_pair_pnl"] == "0.08"
    assert r["residual_shares"] == "0"
    assert r["settlement"] == "UNSETTLED"
    assert r["realized_net"] is None, (
        "a locked pair's P&L is known, but realized_net is reserved for a "
        "venue resolution and must not be invented from it")


def test_an_unsettled_residual_is_never_reported_as_zero():
    r = market_economics({"slug": "m", "fills": [YES_FILL]})
    assert r["residual_side"] == "YES"
    assert r["residual_shares"] == "2"
    assert r["realized_net"] is None
    assert "NOT reported as 0" in r["labels"]
    assert D(r["unsettled_exposure_worst_case"]) == D("-0.90")


def test_settlement_pays_the_right_leg():
    seq = {"slug": "m", "fills": [YES_FILL]}
    won = market_economics(seq, settlement_px=D("1"))
    lost = market_economics(seq, settlement_px=D("0"))
    assert D(won["directional_residual_pnl"]) == D("1.10")   # 2 - 0.90
    assert D(lost["directional_residual_pnl"]) == D("-0.90")
    no_seq = {"slug": "m", "fills": [NO_FILL]}
    # a NO share pays 1 - settlement
    assert D(market_economics(no_seq, D("0"))["directional_residual_pnl"]) \
        == D("0.98")                                          # 2 - 1.02
    assert D(market_economics(no_seq, D("1"))["directional_residual_pnl"]) \
        == D("-1.02")


def test_fees_are_venue_confirmed_and_rebates_are_never_modelled():
    f = dict(YES_FILL, commission="0.0100")
    r = market_economics({"slug": "m", "fills": [f]})
    assert r["fees_venue_confirmed"] == "0.0100"
    assert r["rebates_credited"] == "0"
    assert "not a rebate" in r["rebates_note"]


def test_an_unreadable_direction_is_excluded_not_guessed():
    bad = {"trade_id": "X", "role": "PASSIVE", "shares": "1",
           "wire_px": "0.50"}
    r = market_economics({"slug": "m", "fills": [bad]})
    assert r["fills_unreadable"] and r["YES_shares"] == "0"
    assert r["NO_shares"] == "0"


def test_the_roll_up_separates_settled_from_unsettled():
    audit = {"sequences": [{"slug": "a", "fills": [YES_FILL, NO_FILL]},
                           {"slug": "b", "fills": [YES_FILL]}],
             "market_state": {"a": {"state": "MARKET_STATE_EXPIRED",
                                    "settlementPx": "1.0000"}}}
    r = run(audit)
    assert r["markets"] == 2
    assert r["markets_settled"] == 1
    assert r["markets_unsettled"] == 1
    assert D(r["unsettled_residual_shares"]) == D("2")
    assert r["rebates_credited_total"] == "0"
