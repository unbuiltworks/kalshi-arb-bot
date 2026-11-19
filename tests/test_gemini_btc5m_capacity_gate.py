"""Regressions for the Gemini BTC5m venue capacity gate.

The gate's whole value is that it REFUSES rather than estimates, and that its
fee arithmetic reproduces the venue's rounding exactly. Both are pinned here,
along with the measured 2026-11-19 tape that motivated the module.
"""
from decimal import Decimal as D

import pytest

from src.gemini_btc5m_capacity_gate import (
    BTC05M_WINDOWS_PER_DAY,
    CRYPTO_REBATE_RATE,
    MAKER_RATE,
    TAKER_RATE,
    admits,
    ceil_cent,
    daily_rebate,
    floor_cent,
    maker_fee,
    maker_rebate_accrual,
    min_viable_clip,
    required_share_for_target,
    venue_ceiling,
)

#: the tape measured on 2026-11-19 from the public events endpoint.
#: One BTC05M window completed while sampling and settled at $54.72 of total
#: volume across ALL participants. The touch was sampled 68 times across the
#: live BTC05M windows: MEDIAN SPREAD 1 CENT, quoted 0.50 / 0.51. A single
#: window showed 0.43 / 0.58, and it is the outlier, not the norm -- the
#: 15c reading is kept below as its own case so the two cannot be confused.
MEASURED_WINDOW_USD = D("54.72")
MEASURED_SPREAD = D("0.01")
MEASURED_PRICE = D("0.50")
OUTLIER_SPREAD = D("0.15")
OUTLIER_PRICE = D("0.43")


def _ceiling(**over):
    kw = dict(
        tape_usd_per_window=MEASURED_WINDOW_USD,
        windows_per_day=BTC05M_WINDOWS_PER_DAY,
        traded_window_fraction=D("1"),
        touch_spread=MEASURED_SPREAD,
        quote_price=MEASURED_PRICE,
        participation_share=D("0.10"),
        match_rate=D("0.5"),
        lone_leg_ev_per_contract=D("0"),
        clip_contracts=D("1"),
        maker_fee_waived=True,
    )
    kw.update(over)
    return venue_ceiling(**kw)


# --------------------------------------------------------------- rounding ---

def test_ceil_cent_rounds_up_not_nearest():
    assert ceil_cent(D("0.0000429")) == D("0.01")
    assert ceil_cent(D("0.010001")) == D("0.02")
    assert ceil_cent(D("0.01")) == D("0.01")


def test_floor_cent_rounds_down_not_nearest():
    assert floor_cent(D("0.0099")) == D("0.00")
    assert floor_cent(D("0.019")) == D("0.01")


def test_published_rates_are_the_schedule():
    """A silent rate drift would move every number downstream."""
    assert MAKER_RATE == D("0.0175")
    assert TAKER_RATE == D("0.07")
    assert CRYPTO_REBATE_RATE == D("0.30")


# ------------------------------------------------------------------- fees ---

def test_minimum_clip_pays_a_whole_cent_of_fee():
    """THE finding that kills small size: $0.01 of fee on $0.0043 of notional."""
    fee = maker_fee(contracts=D("0.01"), price=D("0.43"))
    assert fee == D("0.01")
    #: the raw, unrounded fee is four ten-thousandths of a cent
    raw = MAKER_RATE * D("0.01") * D("0.43") * D("0.57")
    assert raw < D("0.0001")


def test_one_contract_fee_is_a_cent_at_mid():
    assert maker_fee(contracts=D("1"), price=D("0.50")) == D("0.01")


def test_waiver_makes_the_fee_exactly_zero():
    assert maker_fee(contracts=D("1"), price=D("0.43"), waived=True) == D("0")


@pytest.mark.parametrize("c,p", [(0, "0.4"), (-1, "0.4"), (1, "0"), (1, "1"),
                                 (1, "1.5"), (None, "0.4"), (1, None)])
def test_fee_refuses_impossible_inputs(c, p):
    assert maker_fee(contracts=c, price=p) is None


# ---------------------------------------------------------------- rebates ---

def test_rebate_is_zero_outside_the_published_band():
    assert maker_rebate_accrual(contracts=D("1"), price=D("0.19")) == D("0")
    assert maker_rebate_accrual(contracts=D("1"), price=D("0.81")) == D("0")
    assert maker_rebate_accrual(contracts=D("1"), price=D("0.20")) > D("0")
    assert maker_rebate_accrual(contracts=D("1"), price=D("0.80")) > D("0")


def test_rebate_uses_the_crypto_rate_times_the_taker_rate():
    got = maker_rebate_accrual(contracts=D("1"), price=D("0.50"))
    assert got == CRYPTO_REBATE_RATE * TAKER_RATE * D("1") * D("0.5") * D("0.5")


def test_a_days_tiny_accrual_pays_nothing_at_all():
    """rebate_program.py's arithmetic: the first cent needs ~1.9 contracts."""
    accrued = maker_rebate_accrual(contracts=D("0.10"), price=D("0.50"))
    assert accrued > D("0")
    assert daily_rebate(accrued) == D("0.00")


def test_the_first_cent_arrives_just_under_two_contracts():
    at_best_price = lambda c: maker_rebate_accrual(contracts=D(c), price=D("0.50"))
    assert daily_rebate(at_best_price("1.9")) == D("0.00")
    assert daily_rebate(at_best_price("2.0")) == D("0.01")


def test_rebate_is_capped_at_five_percent_of_notional():
    #: a huge rate must not out-earn the cap
    got = maker_rebate_accrual(contracts=D("1"), price=D("0.50"),
                               rebate_rate=D("100"))
    assert got == D("0.05") * D("1") * D("0.50")


# ------------------------------------------------------------ sizing floor ---

def test_min_viable_clip_is_two_cents_over_the_spread():
    got = min_viable_clip(touch_spread=OUTLIER_SPREAD, price=OUTLIER_PRICE)
    assert got["ok"] is True
    assert got["min_contracts"] == D("0.02") / OUTLIER_SPREAD
    #: the venue minimum of 0.01 contracts is an order of magnitude too small
    assert got["min_contracts"] > D("0.01")


def test_at_the_median_one_cent_spread_two_whole_contracts_are_needed():
    """THE sizing consequence of a 1c book: a pair's gross is one cent and its
    fees are two, so nothing under two contracts a leg can ever pay."""
    got = min_viable_clip(touch_spread=MEASURED_SPREAD, price=MEASURED_PRICE)
    assert got["ok"] is True
    assert got["min_contracts"] == D("2")


def test_min_viable_clip_has_no_floor_under_the_waiver():
    got = min_viable_clip(touch_spread=OUTLIER_SPREAD, price=OUTLIER_PRICE,
                          maker_fee_waived=True)
    assert got["ok"] is True
    assert got["min_contracts"] is None
    assert got["maker_fee_waived"] is True


def test_a_narrow_spread_needs_a_bigger_clip():
    wide = min_viable_clip(touch_spread=OUTLIER_SPREAD, price=OUTLIER_PRICE)
    narrow = min_viable_clip(touch_spread=D("0.02"), price=OUTLIER_PRICE)
    assert narrow["min_contracts"] > wide["min_contracts"]


# ------------------------------------------- the gate refuses, not guesses ---

@pytest.mark.parametrize("field,reason", [
    ("participation_share", "PARTICIPATION_SHARE_NOT_SET"),
    ("match_rate", "MATCH_RATE_NOT_SET"),
    ("lone_leg_ev_per_contract", "LONE_LEG_EV_NOT_SET"),
    ("traded_window_fraction", "TRADED_WINDOW_FRACTION_NOT_SET"),
])
def test_every_behavioural_rate_is_required(field, reason):
    """NO DEFAULTS. Each of these can manufacture any answer at all."""
    got = _ceiling(**{field: None})
    assert got["ok"] is False
    assert got["reason"] == reason
    assert got["net_usd_per_day"] is None


def test_a_share_above_one_is_refused():
    assert _ceiling(participation_share=D("1.5"))["ok"] is False


def test_spread_wider_than_the_payout_is_refused():
    got = _ceiling(touch_spread=D("1.5"))
    assert got["ok"] is False
    assert got["reason"] == "TOUCH_SPREAD_UNREADABLE"


# ----------------------------------------------------------- the identity ---

def test_matched_pair_gross_margin_is_the_touch_spread():
    """The one identity the module is built on, at three different levels."""
    for spread in ("0.05", "0.15", "0.30"):
        got = _ceiling(touch_spread=D(spread), maker_fee_waived=True)
        assert got["ok"] is True
        assert D(got["gross_per_pair_usd"]) == D(spread)
        assert D(got["pair_cost"]) == D("1") - D(spread)


def test_fees_reduce_the_gross_margin_when_not_waived():
    waived = _ceiling(maker_fee_waived=True)
    charged = _ceiling(maker_fee_waived=False)
    assert D(charged["gross_per_pair_usd"]) < D(waived["gross_per_pair_usd"])
    assert D(charged["net_usd_per_day"]) < D(waived["net_usd_per_day"])


def test_tiny_clip_makes_a_matched_pair_structurally_negative():
    """At the venue minimum the fee exceeds the whole spread, so even a
    perfectly matched book loses money."""
    got = _ceiling(clip_contracts=D("0.01"), maker_fee_waived=False,
                   match_rate=D("1"), lone_leg_ev_per_contract=D("0"))
    assert got["ok"] is True
    assert D(got["gross_per_pair_usd"]) < D("0")
    assert D(got["net_usd_per_day"]) < D("0")


# ------------------------------------------------------ stranded inventory ---

def test_stranded_legs_are_charged_not_ignored():
    free = _ceiling(lone_leg_ev_per_contract=D("0"))
    costly = _ceiling(lone_leg_ev_per_contract=D("-0.05"))
    assert D(costly["net_usd_per_day"]) < D(free["net_usd_per_day"])
    assert D(costly["stranded_usd_per_day"]) < D("0")


def test_a_fully_matched_book_strands_nothing():
    got = _ceiling(match_rate=D("1"), lone_leg_ev_per_contract=D("-0.5"))
    assert D(got["unmatched_contracts_per_day"]) == D("0")
    assert D(got["stranded_usd_per_day"]) == D("0")


def test_one_sided_fills_can_sink_the_whole_lane():
    """Probe A's actual shape: every fill on one side."""
    got = _ceiling(match_rate=D("0"), lone_leg_ev_per_contract=D("-0.05"))
    assert D(got["matched_usd_per_day"]) == D("0")
    assert D(got["net_usd_per_day"]) < D("0")


# ------------------------------------------------------------- feasibility ---

def test_target_480_needs_more_than_the_entire_measured_tape():
    """The decisive arithmetic, on the 2026-11-19 measurement."""
    got = required_share_for_target(
        target_usd_per_day=D("480"),
        tape_usd_per_window=MEASURED_WINDOW_USD,
        windows_per_day=BTC05M_WINDOWS_PER_DAY,
        traded_window_fraction=D("1"),
        touch_spread=MEASURED_SPREAD,
        match_rate=D("1"))
    assert got["ok"] is True
    assert got["feasible"] is False
    assert got["required_share"] > D("1")


def test_a_small_target_is_feasible_on_the_same_tape():
    got = required_share_for_target(
        target_usd_per_day=D("10"),
        tape_usd_per_window=MEASURED_WINDOW_USD,
        windows_per_day=BTC05M_WINDOWS_PER_DAY,
        traded_window_fraction=D("1"),
        touch_spread=MEASURED_SPREAD,
        match_rate=D("1"))
    assert got["feasible"] is True
    assert got["required_share"] < D("1")


def test_required_share_scales_with_the_target():
    base = dict(tape_usd_per_window=MEASURED_WINDOW_USD,
                windows_per_day=BTC05M_WINDOWS_PER_DAY,
                traded_window_fraction=D("1"),
                touch_spread=MEASURED_SPREAD, match_rate=D("1"))
    a = required_share_for_target(target_usd_per_day=D("10"), **base)
    b = required_share_for_target(target_usd_per_day=D("20"), **base)
    #: exact to within Decimal's division precision, not to the last digit
    ratio = b["required_share"] / a["required_share"]
    assert abs(ratio - D("2")) < D("1e-20")


def test_the_headline_number_480_needs_three_times_the_whole_tape():
    """The decision this module exists to make, on the measured 1c book.

    Perfect matching, zero fees, zero adverse selection -- and it still needs
    three times every dollar that trades on the series.
    """
    got = required_share_for_target(
        target_usd_per_day=D("480"),
        tape_usd_per_window=MEASURED_WINDOW_USD,
        windows_per_day=BTC05M_WINDOWS_PER_DAY,
        traded_window_fraction=D("1"),
        touch_spread=MEASURED_SPREAD,
        match_rate=D("1"))
    assert got["ok"] is True
    assert got["feasible"] is False
    assert got["required_share"] > D("3")
    assert D(got["tape_usd_per_day"]) == D("54.72") * D("288")


def test_fewer_traded_windows_raise_the_required_share():
    base = dict(target_usd_per_day=D("10"),
                tape_usd_per_window=MEASURED_WINDOW_USD,
                windows_per_day=BTC05M_WINDOWS_PER_DAY,
                touch_spread=MEASURED_SPREAD, match_rate=D("1"))
    allw = required_share_for_target(traded_window_fraction=D("1"), **base)
    few = required_share_for_target(traded_window_fraction=D("0.1"), **base)
    assert few["required_share"] == allw["required_share"] * D("10")


# ------------------------------------------------------------------ admits ---

def test_admits_refuses_a_ceiling_below_target():
    got = admits(_ceiling(), target_usd_per_day=D("480"))
    assert got["ok"] is False
    assert got["reason"] == "CEILING_BELOW_TARGET"
    assert D(got["shortfall_usd_per_day"]) > D("0")


def test_admits_refuses_an_unknown_ceiling_exactly_as_a_negative_one():
    got = admits(_ceiling(match_rate=None), target_usd_per_day=D("1"))
    assert got["ok"] is False
    assert got["reason"] == "MATCH_RATE_NOT_SET"


def test_admits_refuses_a_nonpositive_ceiling():
    got = admits(_ceiling(match_rate=D("0"),
                          lone_leg_ev_per_contract=D("-0.05")),
                 target_usd_per_day=D("1"))
    assert got["ok"] is False
    assert got["reason"] == "CEILING_NOT_POSITIVE"


def test_admits_passes_a_ceiling_that_clears():
    got = admits(_ceiling(), target_usd_per_day=D("1"))
    assert got["ok"] is True


def test_admits_refuses_junk():
    for junk in (None, {}, "no", 3, {"ok": False, "reason": "X"}):
        assert admits(junk, target_usd_per_day=D("1"))["ok"] is False


# ------------------------------------------------------------ no transport ---

def test_module_has_no_order_or_network_surface():
    """The gate must stay pure: no venue call can hide in a decision module.

    Checked over the AST with every docstring stripped, so the module stays
    free to NAME cancellation and submission in its prose -- which it must, to
    explain what it is not costing -- while being unable to perform either.
    """
    import ast
    import pathlib
    src = pathlib.Path("src/gemini_btc5m_capacity_gate.py").read_text()
    tree = ast.parse(src)

    banned_mods = {"requests", "urllib", "http", "httpx", "websockets",
                   "socket", "aiohttp", "subprocess", "os", "pathlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in banned_mods, a.name
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned_mods, node.module

    #: every name this module actually CALLS. Prose in a docstring or in a
    #: returned note is not a call, and the module must stay free to name
    #: cancellation in order to explain that it is not costing it.
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            called.add(f.id)
        elif isinstance(f, ast.Attribute):
            called.add(f.attr)

    banned_verbs = ("submit", "place_order", "cancel", "amend", "request",
                    "urlopen", "connect", "send", "post")
    for name in sorted(called):
        low = name.lower()
        for verb in banned_verbs:
            assert verb not in low, (name, verb)

    #: and the only calls it makes are arithmetic/helpers we can enumerate
    assert called <= {"_dec", "ceil_cent", "floor_cent", "maker_fee",
                      "maker_rebate_accrual", "daily_rebate", "dict", "str",
                      "bool", "quantize", "is_finite", "D", "Decimal",
                      "get", "isinstance", "no"}, sorted(called)
