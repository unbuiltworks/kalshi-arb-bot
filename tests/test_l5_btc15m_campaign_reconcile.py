"""The basis split, and the detector that has to agree with the ledger.

The only corpus-dependent test is the agreement check, which is skipped when
the ledger file is absent rather than silently passing.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from analysis.l5.btc15m_campaign_reconcile import (LEDGER, PAIR_CEILING, day,
                                                   is_placeholder_basis,
                                                   ledger_agreement, reconcile,
                                                   rescue_decomposition,
                                                   summarize)


def row(up_q="1", up_c="0.40", dn_q="1", dn_c="0.50", mq="1", mc="0.90",
        mp="0.10", rq="0", rp="0", market="cpc-btc-updown-15m-2026-11-05-2200z",
        cost=None, redeem="1.00", settle=None):
    cost = cost if cost is not None else str(D(up_c) + D(dn_c))
    settle = settle if settle is not None else str(D(mp) + D(rp))
    return {"market": market, "up_qty": up_q, "up_cost": up_c,
            "dn_qty": dn_q, "dn_cost": dn_c, "matched_qty": mq,
            "matched_cost": mc, "matched_pnl": mp, "residual_qty": rq,
            "residual_pnl": rp, "cost_total": cost,
            "redemption_total": redeem, "settlement_pnl": settle}


# ----------------------------------------------------- the placeholder flag
def test_a_leg_priced_at_exactly_one_dollar_is_the_placeholder():
    assert is_placeholder_basis(row(dn_q="1.0000", dn_c="1.0000")) is True
    assert is_placeholder_basis(row(up_q="2.0000", up_c="2.0000")) is True


def test_an_ordinary_fill_is_not_the_placeholder():
    assert is_placeholder_basis(row()) is False
    assert is_placeholder_basis(row(up_c="0.99", dn_c="0.01")) is False


def test_a_zero_quantity_leg_cannot_be_a_placeholder():
    assert is_placeholder_basis(row(up_q="0", up_c="0")) is False


def test_a_leg_just_off_a_dollar_is_not_the_placeholder():
    assert is_placeholder_basis(row(up_q="1", up_c="0.9999")) is False
    assert is_placeholder_basis(row(up_q="1", up_c="1.0001")) is False


@pytest.mark.skipif(not os.path.exists(LEDGER), reason="ledger not present")
def test_the_detector_agrees_with_the_ledgers_own_flag_exactly():
    """This is the claim the reconciliation rests on, so it is pinned.

    If the ledger's labelling and the structural detector ever diverge, the
    basis split -- and therefore every per-unit figure derived from it -- is
    no longer trustworthy, and this fails rather than quietly drifting.
    """
    a = ledger_agreement()
    assert a is not None
    assert a["false_positive"] == 0, a
    assert a["false_negative"] == 0, a
    assert a["true_positive"] > 0, a
    assert a["exact"] is True


# ------------------------------------------------------------- the split
def test_placeholder_windows_are_kept_out_of_the_attested_figures():
    harvest = {
        "a": row(market="cpc-btc-updown-15m-2026-11-04-0100z"),
        "b": row(market="cpc-btc-updown-15m-2026-11-05-0100z",
                 dn_q="1.0000", dn_c="1.0000", mp="-0.40"),
    }
    out = reconcile(harvest)
    assert out["counts"] == {"settled": 2, "attested_basis": 1,
                             "placeholder_basis": 1}
    # the attested figure must not carry the placeholder window's -0.40
    assert D(out["summaries"]["attested_all"]["matched_pnl"]) == D("0.10")
    assert D(out["summaries"]["placeholder_all"]["matched_pnl"]) == D("-0.40")


def test_the_mixed_total_is_reported_but_labelled_not_measured():
    harvest = {"a": row(), "b": row(market="cpc-btc-updown-15m-2026-10-20-0100z",
                                    dn_q="1.0000", dn_c="1.0000")}
    out = reconcile(harvest)
    assert "NOT a measured loss" in out["summaries"]["all_mixed"]["label"]
    assert "upper bound" in out["provenance"]["note"]


# ------------------------------------------------------- derived economics
def test_breakeven_ratio_is_residual_cost_over_pair_earnings():
    s = summarize([row(mq="2", mp="0.20", rq="1", rp="-0.50")], "x")
    assert D(s["net_per_matched_pair"]) == D("0.10")
    assert D(s["net_per_residual_unit"]) == D("-0.50")
    assert D(s["matched_to_residual_achieved"]) == D("2")
    assert D(s["matched_to_residual_required"]) == D("5")      # 0.50 / 0.10


def test_no_breakeven_ratio_when_pairs_do_not_earn():
    s = summarize([row(mq="1", mp="-0.01", rq="1", rp="-0.50")], "x")
    assert s["matched_to_residual_required"] is None


def test_paired_fill_rate_counts_windows_not_contracts():
    rows = [row(mq="5", mp="0.50"),
            row(market="cpc-btc-updown-15m-2026-11-05-2215z", mq="0", mc="0",
                mp="0", rq="1", rp="-0.40")]
    s = summarize(rows, "x")
    assert s["paired_fill_rate"] == pytest.approx(0.5)


def test_a_window_with_no_fills_is_not_counted_as_traded():
    s = summarize([row(up_q="0", up_c="0", dn_q="0", dn_c="0", mq="0",
                       mc="0", mp="0")], "x")
    assert s["traded"] == 0 and s["paired_fill_rate"] is None


# -------------------------------------------------- profit vs rescue pairs
def test_pairs_above_the_ceiling_are_rescues_not_spread_capture():
    profit = row(mq="1", mc="0.90", mp="0.10")
    rescue = row(market="cpc-btc-updown-15m-2026-11-05-2300z",
                 mq="1", mc="0.99", mp="0.01")
    dec = rescue_decomposition([profit, rescue])
    assert dec["profit_pairs"]["windows"] == 1
    assert dec["rescue_pairs"]["windows"] == 1
    assert D(dec["profit_pairs"]["net_per_pair"]) == D("0.10")
    assert D(dec["rescue_pairs"]["net_per_pair"]) == D("0.01")


def test_a_pair_exactly_at_the_ceiling_is_a_profit_pair():
    dec = rescue_decomposition([row(mq="1", mc=str(PAIR_CEILING), mp="0.04")])
    assert dec["profit_pairs"]["windows"] == 1
    assert dec["rescue_pairs"]["windows"] == 0


def test_a_blended_multi_round_window_is_flagged_ambiguous():
    dec = rescue_decomposition([row(up_q="3", dn_q="2", mq="2", mc="1.90",
                                    mp="0.10")])
    assert dec["windows_ambiguous_blend"] == 1
    dec2 = rescue_decomposition([row(up_q="1", dn_q="1", mq="1")])
    assert dec2["windows_ambiguous_blend"] == 0


def test_windows_without_a_pair_are_in_neither_bucket():
    dec = rescue_decomposition([row(mq="0", mc="0", mp="0", rq="1", rp="-0.4")])
    assert dec["profit_pairs"]["windows"] == 0
    assert dec["rescue_pairs"]["windows"] == 0


# ------------------------------------------------------------------ dates
def test_day_reads_the_window_date_from_the_slug():
    assert day("cpc-btc-updown-15m-2026-11-05-2200z") == "2026-11-05"
    assert day("no-date-here") == "?"
