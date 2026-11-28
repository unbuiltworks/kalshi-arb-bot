"""The depth frontier, and the two ways arm D could look good on nothing.

A quote four ticks behind the touch always has a wonderful theoretical pair
cost and may never trade. And a trade merely TOUCHING our limit is not evidence
that we filled. Either mistake would make D look profitable on liquidity or
queue priority it never had, so both are pinned here.
"""
from decimal import Decimal as D

import pytest

import analysis.l5.pair_formation.frontier as F
from src.l5_shadow import maker as MK
from src.l5_shadow import two_sided as TS


def _q(depth, *, outcome="Up", venue="PMUS", arm="D", market="m",
       price="0.45", qty="10", crit=None, t=1.0, cohort="BTC15m"):
    fc = {c: False for c in F.CRITERIA}
    if crit:
        for c in F.CRITERIA[:F.CRITERIA.index(crit) + 1]:
            fc[c] = True
    return {"arm": arm, "venue": venue, "market_key": market, "cohort": cohort,
            "desired_outcome": outcome, "ticks_below_touch": depth,
            "quote_price": price, "quote_qty": qty, "placed_at": t,
            "evidenced_fill": crit == F.STRICTEST, "fill_criteria": fc}


# ---------------------------------------------- queue realism is not optional

def test_a_touch_is_not_a_fill():
    """The criterion that would let a deep quote look profitable on liquidity
    it never had."""
    c = MK.fill_criteria(quote_px=D("0.45"), quote_qty=D("10"), ahead=D("0"),
                         later=[{"price": "0.45", "size": "3"}])
    assert c["TOUCH"] is True and c["TRADE_AT_PRICE"] is True
    assert c["VOLUME_AHEAD_CLEARED"] is False
    assert c["strongest_satisfied"] == "TRADE_AT_PRICE"


def test_volume_ahead_of_us_is_charged_before_we_fill():
    c = MK.fill_criteria(quote_px=D("0.45"), quote_qty=D("10"), ahead=D("100"),
                         later=[{"price": "0.45", "size": "50"}])
    assert c["volume_at_price"] == "50" and c["cleared_for_us"] == "0"
    assert c["VOLUME_AHEAD_CLEARED"] is False


def test_a_trade_below_our_bid_is_direct_evidence_the_level_cleared():
    """A seller hits the better bid first, so reaching a lower price means our
    level was exhausted."""
    c = MK.fill_criteria(quote_px=D("0.45"), quote_qty=D("10"), ahead=D("0"),
                         later=[{"price": "0.44", "size": "2"}])
    assert c["TRADE_THROUGH"] is True and c["volume_through"] == "2"


def test_the_strictest_criterion_requires_our_whole_size():
    c = MK.fill_criteria(quote_px=D("0.45"), quote_qty=D("10"), ahead=D("0"),
                         later=[{"price": "0.45", "size": "50"}])
    assert c["VOLUME_AHEAD_CLEARED"] is True and c["cleared_for_us"] == "10"


def test_no_prints_satisfies_nothing():
    c = MK.fill_criteria(quote_px=D("0.45"), quote_qty=D("10"), ahead=D("0"),
                         later=[])
    assert c["strongest_satisfied"] == "NONE"
    assert not any(c[k] for k in F.CRITERIA)


def test_the_evidence_row_never_counts_a_weaker_criterion_as_a_fill():
    r = MK.evidence_for_quote(quote_px="0.45", quote_qty="10", ahead="100",
                              placed_at=0.0,
                              prints=[{"t": 1, "price": "0.45", "size": "50"}],
                              observed_until=300.0, coverage_complete=True)
    assert r["evidenced_fill"] is False
    assert r["fill_criteria"]["TRADE_AT_PRICE"] is True


# ------------------------------------------- deeper is cheaper and thinner

def test_a_depth_that_fills_but_prices_badly_is_too_shallow():
    outs = [_q(0, crit=F.STRICTEST, price="0.60") for _ in range(12)]
    outs += [_q(0, outcome="Down", crit=F.STRICTEST, price="0.55")]
    f = F.frontier(outs, venue="PMUS", arm="D")
    r = f["depths"][0]
    assert r["median_pair_cost"] == "1.150000"
    assert r["verdict"] == "TOO_SHALLOW"
    assert r["above_par_pairs"] == 1


def test_a_depth_that_prices_well_but_never_fills_is_too_deep():
    outs = [_q(4, crit="TOUCH") for _ in range(12)]
    f = F.frontier(outs, venue="PMUS", arm="D")
    r = f["depths"][0]
    assert r["first_fill_probability"] == 0.0
    assert r["two_sided_windows"] == 0
    assert r["verdict"] == "TOO_DEEP"


def test_a_depth_that_both_fills_and_prices_inside_the_ceiling_is_viable():
    outs = [_q(2, crit=F.STRICTEST, price="0.45", market=f"m{i}")
            for i in range(12)]
    outs += [_q(2, outcome="Down", crit=F.STRICTEST, price="0.50",
                market=f"m{i}") for i in range(12)]
    f = F.frontier(outs, venue="PMUS", arm="D")
    r = f["depths"][0]
    assert r["verdict"] == "VIABLE"
    assert r["median_pair_cost"] == "0.950000"
    assert f["sweet_spot"]["resting_depth_ticks"] == 2


def test_the_sweet_spot_is_the_shallowest_viable_depth_not_the_cheapest():
    outs = []
    for depth, up, dn in ((1, "0.46", "0.49"), (3, "0.44", "0.47")):
        for i in range(12):
            outs.append(_q(depth, crit=F.STRICTEST, price=up,
                           market=f"m{depth}_{i}"))
            outs.append(_q(depth, outcome="Down", crit=F.STRICTEST, price=dn,
                           market=f"m{depth}_{i}"))
    f = F.frontier(outs, venue="PMUS", arm="D")
    assert {r["verdict"] for r in f["depths"]} == {"VIABLE"}
    assert f["sweet_spot"]["resting_depth_ticks"] == 1     # not the cheaper 3
    assert "never the cheapest" in F.report("/nonexistent")["labels"]


def test_too_few_decided_quotes_decides_nothing():
    outs = [_q(1, crit=F.STRICTEST) for _ in range(3)]
    assert F.frontier(outs, venue="PMUS", arm="D")["depths"][0]["verdict"] \
        == "INSUFFICIENT_DATA"


# --------------------- the live ceiling does not censor the experiment

def test_pairs_above_the_live_ceiling_are_measured_and_labelled():
    outs = [_q(0, crit=F.STRICTEST, price="0.50", market=f"m{i}")
            for i in range(12)]
    outs += [_q(0, outcome="Down", crit=F.STRICTEST, price="0.48",
                market=f"m{i}") for i in range(12)]
    r = F.frontier(outs, venue="PMUS", arm="D")["depths"][0]
    assert r["median_pair_cost"] == "0.980000"
    assert r["research_ineligible_pairs"] == 12
    assert r["pairs_within_live_ceiling"] == 0
    assert r["pair_economics_label"] == F.RESEARCH_LABEL
    assert "unfalsifiable" in r["why_label"]
    assert r["verdict"] == "TOO_SHALLOW_FOR_LIVE"      # measured, not deleted


def test_an_above_ceiling_depth_still_reports_its_fill_probability():
    outs = [_q(0, crit=F.STRICTEST, price="0.50") for _ in range(12)]
    r = F.frontier(outs, venue="PMUS", arm="D")["depths"][0]
    assert r["first_fill_probability"] == 1.0


# ------------------------------------------ every rate at every criterion

def test_a_depth_viable_only_under_touch_is_not_viable_under_the_strictest():
    outs = []
    for i in range(12):
        outs.append(_q(3, crit="TOUCH", price="0.44", market=f"m{i}"))
        outs.append(_q(3, outcome="Down", crit="TOUCH", price="0.50",
                       market=f"m{i}"))
    loose = F.frontier(outs, venue="PMUS", arm="D", criterion="TOUCH")
    strict = F.frontier(outs, venue="PMUS", arm="D", criterion=F.STRICTEST)
    assert loose["depths"][0]["verdict"] == "VIABLE"
    assert strict["depths"][0]["verdict"] == "TOO_DEEP"
    assert strict["depths"][0]["first_fill_probability"] == 0.0


def test_the_taker_regime_cohorts_never_enter_the_frontier():
    outs = [_q(1, crit=F.STRICTEST, cohort="BTC5m") for _ in range(12)]
    assert F.frontier(outs, venue="PMUS", arm="D")["depths"] == []


# --------------------------------------------- report early, not at 20

def test_the_first_d_leg_and_first_pair_are_flagged_immediately():
    outs = [_q(1, crit=F.STRICTEST)]
    s = F.signals(outs, {})
    assert s["first_D_leg_filled"] is True and s["report_now"] is True
    assert "not for telling you something changed" in s["why"]


def test_nothing_yet_is_not_a_signal():
    s = F.signals([], {})
    assert s["report_now"] is False and s["first_D_pair_completed"] is False
