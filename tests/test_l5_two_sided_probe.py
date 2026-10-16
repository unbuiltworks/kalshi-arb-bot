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

def test_resting_AT_the_touch_is_now_ACCEPTED_as_a_post_only_pair(armed):
    """pair 0.99 is negative as a taker pair and positive as a maker one.
    Both legs are post-only by construction, so the maker coefficient is the
    one that applies -- and the taker figure is still reported."""
    r = TS.build_orders(**book(depth_ticks=0))
    assert r["ok"] is True, r
    assert r["pair_cost"] == "0.99"
    ff = r["fee_floor"]
    assert D(ff["net_if_maker"]) > 0
    assert D(ff["net_if_taker"]) < 0        # the honest worst case, reported
    assert ff["requires_post_only"] is True


def test_a_pair_at_or_above_a_dollar_is_still_refused(armed):
    """No fee schedule makes a par pair worth placing."""
    r = TS.build_orders(**book(bid=D("0.50"), ask=D("0.50"), depth_ticks=0))
    assert r["ok"] is False
    assert r["reason"] in ("BOOK_CROSSED_OR_LOCKED", "EDGE_BELOW_FEE_FLOOR")
    r2 = TS.fee_floor(D("0.50"), D("0.50"))
    assert r2["edge"] == 0 and r2["ok"] is False


def test_the_floor_uses_the_ACTUAL_quote_prices_not_a_constant(armed):
    """The fee is Theta*p*(1-p), so the requirement at the extremes is less
    than half what it is at 50/50. A constant floor cannot express that."""
    mid = TS.fee_floor(D("0.50"), D("0.49"))
    ext = TS.fee_floor(D("0.88"), D("0.11"))
    assert mid["edge"] == ext["edge"] == D("0.01")
    assert ext["taker_fee_per_contract"] < mid["taker_fee_per_contract"] / 2
    # both are placeable as post-only pairs; neither is as a taker pair
    assert mid["ok"] and ext["ok"]
    assert mid["net_if_taker"] < 0 and ext["net_if_taker"] < 0


def test_the_floor_verdict_is_explicitly_post_only_only(armed):
    f = TS.fee_floor(D("0.60"), D("0.39"))
    assert f["requires_post_only"] is True
    assert "POST-ONLY" in f["labels"]
    # and the orders the module emits really are post-only
    r = TS.build_orders(**book(depth_ticks=0))
    for leg in r["legs"]:
        assert leg["post_only"] is True
        assert leg["mode"] == TS.MODE == FT.MAKER


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


# ------------------------------------------- continuous market authorization

def test_an_exact_slug_is_still_exact(monkeypatch):
    """Nothing already configured widens. A pattern is opt-in."""
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "e")
    monkeypatch.setenv(TS.MARKET_ENV, MKT)
    assert TS.active(MKT) is True
    assert TS.active(MKT + "x") is False
    assert TS.active("cpc-btc-updown-15m-2026-10-15-1745z") is False


def test_a_family_pattern_carries_across_successive_windows(monkeypatch):
    """The runner builds a NEW slug every fifteen minutes, so an exact-slug
    authorization goes inert on the roll. A family pattern is what lets
    accumulation continue into the next market without a human edit."""
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "e")
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")
    assert TS.active("cpc-btc-updown-15m-2026-10-16-2100z") is True
    assert TS.active("cpc-btc-updown-15m-2026-10-16-2115z") is True
    # and it is NOT "any market": a different instrument is not covered
    assert TS.active("cpc-btc-updown-1d-2026-10-16-2100z") is False
    assert TS.active("cpceth-eth-updown-15m-2026-10-16-2100z") is False


def test_several_patterns_may_be_authorized(monkeypatch):
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "e")
    monkeypatch.setenv(TS.MARKET_ENV,
                       "cpc-btc-updown-15m-*, cpceth-eth-updown-15m-*")
    assert TS.active("cpceth-eth-updown-15m-2026-10-16-2100z") is True
    assert TS.active("cpcsol-sol-updown-15m-2026-10-16-2100z") is False


def test_an_empty_or_comma_only_market_is_inert(monkeypatch):
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "e")
    for raw in ("", "   ", ",", " , , "):
        monkeypatch.setenv(TS.MARKET_ENV, raw)
        assert TS.active(MKT) is False
        assert TS.market_patterns() == ()


def test_matching_is_case_sensitive(monkeypatch):
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "e")
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")
    assert TS.active("CPC-BTC-UPDOWN-15M-2026-10-16-2100z") is False


# == inventory-aware maker quoting (SHADOW/REPORT ONLY) ===================
# The verified gap: build_orders priced both legs from the book alone and
# never saw what we hold or what it cost, while completion_decision already
# took up_qty/dn_qty/up_cost_px/dn_cost_px. These pin the bridge.

def _iaq(**kw):
    base = dict(up_qty=0, dn_qty=0, up_cost_px=None, dn_cost_px=None,
                blind_up_px="0.64", blind_dn_px="0.33", tick="0.01")
    base.update(kw)
    return TS.inventory_aware_quote(**base)


def test_flat_or_balanced_quotes_symmetric():
    for a, b in ((0, 0), (5, 5), ("2.5", "2.5")):
        r = _iaq(up_qty=a, dn_qty=b)
        assert r["ok"] and r["deficient"] is None
        assert r["action"] == "QUOTE_SYMMETRIC" and r["reprice"] is False


def test_cheap_inventory_opens_headroom_on_the_completing_side():
    """Long 300 UP at 0.59: DOWN may pay up to breakeven - 0.59."""
    r = _iaq(up_qty=300, dn_qty=0, up_cost_px="0.59")
    assert r["deficient"] == "DOWN"
    assert r["max_completing_px"] == "0.39"      # 0.9856.. - 0.59, floored
    assert r["blind_px"] == "0.33"
    assert r["headroom_ticks"] == "6"
    assert r["blind_quote_completes_profitably"] is True


def test_queue_priority_is_PRESERVED_when_the_blind_quote_still_pays():
    """Headroom alone is NOT a reason to reprice: measured on 2030z,
    re-joining a ~2,600-deep queue cleared in 3 of 143 intervals."""
    r = _iaq(up_qty=300, dn_qty=0, up_cost_px="0.59")
    assert r["reprice"] is False
    assert r["action"] == "HOLD_PRIORITY"


def test_reprice_only_when_the_blind_quote_would_complete_INTO_A_LOSS():
    """Long 300 UP at 0.98: the blind DOWN quote of 0.33 makes a 1.31 pair."""
    r = _iaq(up_qty=300, dn_qty=0, up_cost_px="0.98")
    assert r["reprice"] is True
    assert r["action"] == "NO_PROFITABLE_COMPLETION"
    # 0.9856.. - 0.98 = 0.0057, which floors to 0.00 -- arithmetically a
    # completing price exists, but not one the venue would accept.
    assert r["reason"] == "COMPLETING_PRICE_BELOW_MIN_PRICE"
    assert r["max_completing_px"] == "0.00"


def test_the_2030z_state_reproduces_the_measured_headroom():
    """Long 1 DOWN at 0.33 -- the real one-sided state at t0+7.5s."""
    r = _iaq(up_qty=0, dn_qty=1, dn_cost_px="0.33")
    assert r["deficient"] == "UP"
    assert r["max_completing_px"] == "0.65"
    assert r["blind_px"] == "0.64"
    assert r["headroom_ticks"] == "1"
    assert r["action"] == "HOLD_PRIORITY"


def test_inventory_present_with_no_cost_is_refused_not_read_as_free():
    for kw in (dict(up_qty=300, dn_qty=0, up_cost_px=None),
               dict(up_qty=300, dn_qty=0, up_cost_px="0")):
        r = _iaq(**kw)
        assert r["ok"] is False and r["reason"] == "HELD_COST_UNREADABLE"


def test_unreadable_inventory_and_negative_quantities_refuse():
    assert _iaq(up_qty=None, dn_qty=None)["reason"] == "INVENTORY_UNREADABLE"
    assert _iaq(up_qty=-1, dn_qty=0)["reason"] == "NEGATIVE_INVENTORY"
    assert _iaq(up_qty=1, dn_qty=0, up_cost_px="0.5",
                tick=None)["reason"] == "INVENTORY_UNREADABLE"


def test_the_completing_price_is_quantised_DOWN_to_the_grid():
    r = _iaq(up_qty=1, dn_qty=0, up_cost_px="0.5")
    # 0.98566.. - 0.5 = 0.48566.. -> 0.48, never 0.49
    assert r["max_completing_px"] == "0.48"


def test_build_orders_is_BYTE_IDENTICAL_without_inventory(monkeypatch):
    monkeypatch.setenv("L5_TS_MARKET", "*")
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "x")
    kw = dict(market="cpc-btc-updown-15m-2026-10-16-2030z", bid="0.65",
              ask="0.66", bid_depth="4951", ask_depth="2141", tick="0.01",
              min_qty="0.01", depth_ticks=1, cash_envelope="1",
              max_worst_case_loss="5", book_age_s=0.1)
    a = TS.build_orders(**kw)
    b = TS.build_orders(**kw, up_qty=0, dn_qty=1, dn_cost_px="0.33")
    assert a["ok"] and b["ok"]
    assert "inventory_aware" not in a, "the live path must be untouched"
    assert "inventory_aware" in b
    assert {k: v for k, v in b.items() if k != "inventory_aware"} == a
    # and the QUOTE itself is unchanged -- this reports, it does not reprice
    assert [l["price"] for l in a["legs"]] == [l["price"] for l in b["legs"]]
