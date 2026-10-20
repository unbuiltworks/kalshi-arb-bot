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


# --------------------------------------------------- the 2026-10-19 bracket

def test_the_cross_price_bracket_separates_queue_from_price_distance():
    """THE OCT 19 FILL. A bid at 0.89 under a 0.93 book filled for real.
    `ahead_of_us` said 12959.03; the same-rung queue was 604.00. The bracket
    must report both and must not turn either into a fill."""
    from decimal import Decimal as D
    from src import l5_maker_fill_model as FM
    lad = FM.Ladder()
    lad.ingest({"market_events": [{
        "marketSlug": "S",
        "bids": [{"px": "0.93", "qty": "100"}, {"px": "0.92", "qty": "4000"},
                 {"px": "0.91", "qty": "4000"}, {"px": "0.90", "qty": "4255.03"},
                 {"px": "0.89", "qty": "604"}],
        "asks": [{"px": "0.94", "qty": "7768.28"}]}]}, 1000.0)
    br = lad.cross_price_bracket("S", FM.BID, D("0.89"), D("0.31"))
    assert br["same_rung_fifo"] == "604"
    assert D(br["cross_price_depth"]) == D("12355.03")
    assert D(br["legacy_ahead_of_us"]) == D("12959.03")
    # the material correction: 604.31 of same-rung volume, not 12959.34
    assert D(br["conservative_required_volume"]) == D("604.31")
    assert D(br["legacy_required_volume"]) == D("12959.34")
    assert br["requires_price_to_reach_us"] is True
    assert "withdrawal" in br["labels"]


def test_the_bracket_changes_no_fill_decision():
    """It is a diagnostic. An unknown or deep queue still does not fill."""
    from decimal import Decimal as D
    from src import l5_maker_fill_model as FM
    lad = FM.Ladder()
    lad.ingest({"market_events": [{"marketSlug": "S",
                                   "bids": [{"px": "0.50", "qty": "10"}],
                                   "asks": [{"px": "0.51", "qty": "10"}]}]},
               1000.0)
    before = lad.ahead_of_us("S", FM.BID, D("0.50"))
    lad.cross_price_bracket("S", FM.BID, D("0.50"), D("1"))
    assert lad.ahead_of_us("S", FM.BID, D("0.50")) == before
    # and at a price the venue never published, the bracket refuses to guess
    br = lad.cross_price_bracket("S", FM.BID, D("0.01"), D("1"))
    assert br["same_rung_fifo"] is None
    assert br["why"] == "no_published_depth_at_our_price"


def test_an_expired_authorization_leaves_the_two_leg_path_inert(monkeypatch):
    """The two-leg path must not place when the owner's deadline has lapsed.
    Oct 19 proved the live refusal; this pins it as a test."""
    import datetime as dt
    from src import l5_live_authorization as AUTH
    monkeypatch.setenv(AUTH.NOT_AFTER_ENV, "2026-10-19T13:29:00Z")
    st = AUTH.not_after_status(now=dt.datetime(2026, 10, 20, 1, 0,
                                               tzinfo=dt.timezone.utc))
    assert st["set"] is True
    assert st["state"] == "EXPIRED"
    assert st["expires_in_s"] < 0
    # the configured value is never emitted
    assert "2026-10-19T13:29:00Z" not in repr(st)


def test_both_legs_plus_fees_stay_inside_the_five_dollar_obligation(monkeypatch):
    """The cash gate charges a resting DOWN leg a dollar a share, so the
    binding number is the obligation, not the gross cash."""
    from decimal import Decimal as D
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "bound-check")
    monkeypatch.setenv("L5_TS_MARKET", "cpc-btc-updown-15m-*")
    from src import l5_two_sided_probe as TSP
    r = TSP.build_orders(market="cpc-btc-updown-15m-2026-10-20-0100z",
                         bid="0.49", ask="0.51",
                         bid_depth="800", ask_depth="800",
                         tick="0.01", min_qty="0.01", depth_ticks=1,
                         cash_envelope="5", max_worst_case_loss="5",
                         book_age_s="0.1")
    assert r["ok"] is True
    assert D(r["cash_gate_obligation_usd"]) <= D("5")
    assert D(r["gross_cash_usd"]) <= D("5")
    assert abs(D(r["worst_single_leg_loss_usd"])) <= D("5")
    # profitable on BOTH outcomes charging the TAKER fee and crediting NO rebate
    assert r["both_positive_if_taker"] is True
    assert D(r["up_pnl_if_taker"]) > 0 and D(r["dn_pnl_if_taker"]) > 0
    # the venue grid is respected
    assert D(r["qty_per_leg"]) % D("0.01") == 0


def test_the_cancel_margin_widens_but_never_narrows(monkeypatch):
    """The watchdog calls an order older than 300s overdue and HALTS before
    cancelling, so the terminal margin must be able to cancel EARLIER."""
    from src import l5_two_sided_probe as TSP
    end = 1792000900.0
    assert TSP.cancel_margin_s() == TSP.CANCEL_MARGIN_S
    monkeypatch.setenv(TSP.CANCEL_MARGIN_ENV, "300")
    assert TSP.cancel_margin_s() == 300.0
    assert TSP.cancel_cutoff(end) == end - 300.0
    # narrowing is refused: a smaller value cannot push the deadline later
    monkeypatch.setenv(TSP.CANCEL_MARGIN_ENV, "10")
    assert TSP.cancel_margin_s() == TSP.CANCEL_MARGIN_S
    # and an unreadable value is not permission
    monkeypatch.setenv(TSP.CANCEL_MARGIN_ENV, "not-a-number")
    assert TSP.cancel_margin_s() == TSP.CANCEL_MARGIN_S


def test_the_widened_margin_keeps_a_leg_inside_the_watchdog_limit(monkeypatch):
    """With the margin at 300 and the lock lifted at T+6:00, a leg placed by
    the T+8:00 cutoff rests at most 240s -- 60s inside the watchdog's 300s."""
    from src import l5_two_sided_probe as TSP
    monkeypatch.setenv(TSP.CANCEL_MARGIN_ENV, "300")
    start = 1792000000.0
    end = TSP.window_end(start)
    terminal = TSP.cancel_cutoff_from_start(start)
    place_last = TSP.place_cutoff(start, end)
    earliest_place = start + 360.0            # lock lifted at T+6:00
    assert terminal - earliest_place <= 300.0
    assert terminal - place_last >= TSP.MIN_REST_S
    assert place_last < terminal
