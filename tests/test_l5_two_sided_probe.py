"""The one-window two-sided BTC15m probe: inert by default, no fallback."""
import os
from decimal import Decimal as D

import pytest

from src import l5_faithful_translation as FT
from src import l5_order_body as OB
from src import l5_two_sided_probe as TS

MKT = "cpc-btc-updown-15m-2026-10-15-1730z"


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "ts1.probe")
    monkeypatch.setenv(TS.MARKET_ENV, MKT)


def book(**over):
    b = {"market": MKT, "bid": D("0.45"), "ask": D("0.46"),
         "bid_depth": D("800"), "ask_depth": D("5000"),
         "tick": D("0.01"), "min_qty": D("0.01"),
         "depth_ticks": 2, "cash_envelope": D("26"),
         "max_worst_case_loss": D("10"), "book_age_s": D("0.1")}
    b.update(over)
    return b


# ------------------------------------------------------------ inertness

def test_inert_with_nothing_configured(monkeypatch):
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    assert TS.active(MKT) is False
    assert TS.build_orders(**book())["reason"] == "NOT_THE_AUTHORIZED_MARKET"


@pytest.mark.parametrize("which", [TS.EXPERIMENT_ENV, TS.MARKET_ENV])
def test_a_half_configuration_cannot_half_arm_it(monkeypatch, which):
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    monkeypatch.setenv(which, "something")
    assert TS.active(MKT) is False


def test_another_market_is_untouched(armed):
    other = "cpc-btc-updown-15m-2026-10-15-1745z"
    assert TS.active(other) is False
    assert TS.build_orders(**book(market=other))["reason"] == \
        "NOT_THE_AUTHORIZED_MARKET"


# -------------------------------------------- the structural refusal

def test_resting_AT_the_touch_is_refused_because_it_loses_both_ways(armed):
    """pair = 1 - spread = 0.99 on a one-tick book: 1 cent of edge against a
    3.44-cent worst-case fee. The probe must not place it."""
    r = TS.build_orders(**book(depth_ticks=0))
    assert r["ok"] is False
    assert r["reason"] == "EDGE_BELOW_FEE_FLOOR"
    assert r["pair_cost"] == "0.99"


def test_one_tick_behind_is_still_refused(armed):
    r = TS.build_orders(**book(depth_ticks=1))
    assert r["ok"] is False and r["reason"] == "EDGE_BELOW_FEE_FLOOR"


def test_two_ticks_behind_clears_the_fee_floor_and_pays_on_both(armed):
    r = TS.build_orders(**book(depth_ticks=2))
    assert r["ok"] is True, r
    assert r["pair_cost"] == "0.95"
    assert D(r["up_pnl"]) > 0 and D(r["dn_pnl"]) > 0
    legs = {l["side"]: l for l in r["legs"]}
    assert legs["UP"]["price"] == "0.43"
    assert legs["DOWN"]["price"] == "0.52"
    for l in r["legs"]:
        assert l["post_only"] is True
        assert l["mode"] == FT.MAKER
        # and the wire body really is post-only
        assert OB.from_intent(dict(l, market=MKT), MKT)[
            "participateDontInitiate"] is True


# ------------------------------------------------------------- sizing

def test_the_worst_single_leg_fill_stays_inside_the_loss_ceiling(armed):
    r = TS.build_orders(**book(max_worst_case_loss=D("10")))
    assert abs(D(r["worst_single_leg_loss_usd"])) <= D("10")


def test_the_minimal_four_dollar_probe_sizes_down_not_out(armed):
    r = TS.build_orders(**book(cash_envelope=D("4"),
                               max_worst_case_loss=D("2")))
    assert r["ok"] is True
    assert D(r["gross_cash_usd"]) <= D("4")
    assert abs(D(r["worst_single_leg_loss_usd"])) <= D("2")
    assert D(r["qty_per_leg"]) < D("5")


def test_an_envelope_too_small_for_the_grid_refuses(armed):
    r = TS.build_orders(**book(cash_envelope=D("0.001"),
                               max_worst_case_loss=D("0.001")))
    assert r["ok"] is False and r["reason"] == "NO_LEGAL_QUANTITY"


# ------------------------------------------------- fail closed inputs

@pytest.mark.parametrize("over,reason", [
    ({"bid": None}, "BOOK_NOT_TWO_SIDED"),
    ({"ask": None}, "BOOK_NOT_TWO_SIDED"),
    ({"bid": D("0.46"), "ask": D("0.45")}, "BOOK_CROSSED_OR_LOCKED"),
    ({"tick": None}, "TICK_UNKNOWN"),
    ({"min_qty": None}, "MIN_QTY_UNKNOWN"),
    ({"book_age_s": None}, "BOOK_STALE"),
    ({"book_age_s": D("9")}, "BOOK_STALE"),
    ({"gap_suspected": True}, "GAP_SUSPECTED"),
    ({"cash_envelope": None}, "CASH_ENVELOPE_UNKNOWN"),
    ({"max_worst_case_loss": None}, "LOSS_CEILING_UNKNOWN"),
    ({"bid_depth": None}, "NO_DISPLAYED_DEPTH"),
    ({"depth_ticks": -1}, "DEPTH_TICKS_UNKNOWN"),
])
def test_every_unreadable_input_refuses_and_returns_no_legs(armed, over,
                                                            reason):
    r = TS.build_orders(**book(**over))
    assert r["ok"] is False
    assert r["reason"] == reason
    assert "legs" not in r              # never a partial order set


def test_a_price_below_the_band_refuses(armed):
    r = TS.build_orders(**book(bid=D("0.07"), ask=D("0.08"), depth_ticks=3))
    assert r["ok"] is False
    assert r["reason"] in ("PRICE_OUT_OF_BAND", "EDGE_BELOW_FEE_FLOOR")


# ---------------------------------------------------------- economics

def test_both_outcome_pnl_for_the_owners_asymmetric_shape():
    """20 UP @0.70 + 25 DOWN @0.20, fees off: cost 19, +1 and +6."""
    e = TS.net_settlement(up_qty=D(20), dn_qty=D(25), up_price=D("0.70"),
                          dn_price=D("0.20"), theta=D(0))
    assert e["cost"] == D(19)
    assert e["up_pnl"] == D(1) and e["dn_pnl"] == D(6)
    assert e["both_positive"] is True


def test_conservative_fees_can_flip_a_thin_shape_negative():
    thin = dict(up_qty=D(10), dn_qty=D(10), up_price=D("0.45"),
                dn_price=D("0.54"))
    assert TS.net_settlement(theta=D(0), **thin)["both_positive"] is True
    assert TS.net_settlement(**thin)["both_positive"] is False


def test_excess_on_the_expensive_side_is_rejected_as_a_top_up():
    r = TS.top_up(up_qty=D(10), dn_qty=D(10), up_price=D("0.43"),
                  dn_price=D("0.52"), extra_side="UP", extra_qty=D(5),
                  extra_price=D("0.43"))
    assert r["ok"] is False
    assert r["reason"] == "TOP_UP_WORSENS_WORST_OUTCOME"
    assert r["worst_after"] < r["worst_before"]


def test_cheap_excess_on_the_lagging_side_is_accepted_as_a_top_up():
    """UP 18 filled, DOWN only 9: cheap DOWN lifts min(a,b) and the worst
    outcome with it."""
    r = TS.top_up(up_qty=D(18), dn_qty=D(9), up_price=D("0.43"),
                  dn_price=D("0.52"), extra_side="DOWN", extra_qty=D(9),
                  extra_price=D("0.20"))
    assert r["ok"] is True
    assert r["worst_after"] > r["worst_before"]
    assert r["both_positive_after"] is True


def test_a_top_up_of_unknown_side_or_size_refuses():
    assert TS.top_up(up_qty=D(1), dn_qty=D(1), up_price=D("0.4"),
                     dn_price=D("0.5"), extra_side="SIDEWAYS",
                     extra_qty=D(1), extra_price=D("0.1"))["ok"] is False
    assert TS.top_up(up_qty=D(1), dn_qty=D(1), up_price=D("0.4"),
                     dn_price=D("0.5"), extra_side="DOWN",
                     extra_qty=D(0), extra_price=D("0.1"))["ok"] is False


# -------------------------------------------------------- fill models

def test_expected_value_charges_the_single_leg_outcomes():
    ev = TS.expected_value(matched_edge=D("0.0156"),
                           residual_alpha=D("0.126"),
                           p_first=D("0.5"), p_complete=D("0.5"))
    assert ev["p_both"] == D("0.25")
    assert ev["p_one_leg"] == D("0.50")
    assert ev["ev"] < 0                   # residual dominates at coin flips


def test_the_minimum_completion_probability_is_high_and_computed():
    """e = 0.0156 (taker fees), alpha = 0.126 measured from L5's own
    unmatched inventory -> break-even needs ~94% on both legs."""
    p = TS.min_completion_probability(matched_edge=D("0.0156"),
                                      residual_alpha=D("0.126"))
    assert p is not None
    assert D("0.93") < p < D("0.95")
    # the rebate upside lowers the bar but nowhere near a coin flip
    p2 = TS.min_completion_probability(matched_edge=D("0.0562"),
                                       residual_alpha=D("0.126"))
    assert D("0.80") < p2 < D("0.83")


def test_a_free_residual_needs_no_completion_probability():
    assert TS.min_completion_probability(matched_edge=D("0.05"),
                                         residual_alpha=D(0)) == D(0)


def test_displayed_depth_is_never_treated_as_a_fill(armed):
    """The success record must say so in terms, because this is the single
    assumption that has produced every false positive so far."""
    r = TS.build_orders(**book())
    assert "displayed prices are NOT fills" in r["labels"]


# ------------------------------------------------------------- timing

def test_the_terminal_deadline_and_place_cutoff_are_ordered():
    t0 = 1_792_000_000.0
    t1 = t0 + TS.WINDOW_S
    assert TS.cancel_cutoff(t1) == t1 - 180.0
    assert TS.place_cutoff(t0, t1) <= TS.cancel_cutoff(t1) - TS.MIN_REST_S
    assert TS.may_place(t0 + 10, t0, t1) is None
    assert TS.may_place(t0 - 1, t0, t1) == "WINDOW_NOT_OPEN"
    assert TS.may_place(TS.place_cutoff(t0, t1), t0, t1) == "PAST_PLACE_CUTOFF"


def test_a_short_window_clamps_the_place_cutoff_not_the_deadline():
    t0 = 1_792_000_000.0
    t1 = t0 + 300                        # 5 minutes only
    assert TS.place_cutoff(t0, t1) < TS.cancel_cutoff(t1)


# ------------------------------------------------- module owns nothing

def test_the_module_cannot_submit_cancel_or_settle():
    import inspect
    src = inspect.getsource(TS)
    # the prose explains what it does NOT own, so scan the CODE only
    if TS.__doc__:
        src = src.replace(TS.__doc__, "")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    for forbidden in ("transport", "submit(", ".cancel(", "requests",
                      "urllib", "http", "open("):
        assert forbidden not in code, forbidden


def test_fees_round_a_cost_up_and_a_credit_down_in_magnitude():
    cost = TS.fee(TS.TAKER_THETA, D("0.22"), D("0.5"))
    cred = TS.fee(TS.MAKER_THETA, D("0.22"), D("0.5"))
    assert cost == D("0.01")             # 0.00382 -> a whole cent of cost
    assert cred == D("0.00")             # -0.00069 -> no credit assumed
    assert TS.fee(TS.MAKER_THETA, D(10), D("0.5")) == D("-0.03")
