"""Three mechanism studies: what the venue was doing, not which price we chose."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import mechanism as MX


# ======================================================== 1. CLOSEST APPROACH

def _cyc(ms, bid, ask, levels=None):
    return {"utc_ms": ms,
            "book": {"readable": True, "yes": {"bid": bid, "ask": ask},
                     "no": {"bid": "0.30", "ask": "0.40"}},
            "depth": ({"readable": True, "bids": {"levels": levels}}
                      if levels is not None else {"readable": False})}


def _ep(price="0.40", lo=0, hi=100000, leg="YES"):
    return {"leg": leg, "price": price, "placed_ms": lo, "ended_ms": hi,
            "rested_s": 60.0}


def test_CLOSEST_GAP_IS_MEASURED_AGAINST_THE_BEST_MAKER_PRICE():
    """One tick under the ask. At or above the ask is a cross and is not
    territory we may enter, so it cannot count as an approach."""
    a = MX.approach(_ep(price="0.40"), [_cyc(10, "0.40", "0.45")], [])
    # best maker is 0.44, which is 4 ticks above our 0.40
    assert a["closest_gap_ticks"] == 4.0
    assert a["bucket"] == MX.WITHIN_5


def test_the_buckets_split_by_how_close_it_ever_came():
    within2 = MX.approach(_ep(), [_cyc(10, "0.40", "0.43")], [])
    never = MX.approach(_ep(), [_cyc(10, "0.40", "0.90")], [])
    assert within2["bucket"] == MX.WITHIN_2
    assert never["bucket"] == MX.NEVER
    assert never["closest_gap_ticks"] > 5


def test_touch_reached_without_an_execution_is_its_own_bucket():
    """Maker territory covered our price and still nothing traded into it.
    That is a different fact from never getting close."""
    a = MX.approach(_ep(price="0.40"), [_cyc(10, "0.39", "0.41")], [])
    assert a["closest_gap_ticks"] <= 0
    assert a["bucket"] == MX.TOUCH_NO_EXEC
    assert a["reaching_prints_in_the_quotes_life"] == 0


def test_a_reaching_print_makes_a_maker_fill_historically_possible():
    prints = [{"utc_ms": 50, "type": "sell", "price": "0.38", "amount": "10"}]
    a = MX.approach(_ep(price="0.40"), [_cyc(10, "0.40", "0.90")], prints)
    assert a["bucket"] == MX.MAKER_POSSIBLE
    assert a["reaching_prints_in_the_quotes_life"] == 1


def test_NO_OPPOSITE_BOOK_IS_UNMEASURED_NOT_ZERO():
    a = MX.approach(_ep(), [], [])
    assert a["bucket"] == MX.NO_BOOK
    assert "not zero" in a["why"]


def test_STRICT_CHRONOLOGY_books_outside_the_quotes_life_are_ignored():
    cycles = [_cyc(5, "0.40", "0.41"),        # before placement
              _cyc(500, "0.40", "0.90"),      # inside
              _cyc(99999, "0.40", "0.41")]    # after it came down
    a = MX.approach(_ep(lo=100, hi=1000), cycles, [])
    assert a["observations"] == 1
    assert a["bucket"] == MX.NEVER


def test_prints_outside_the_quotes_life_do_not_make_a_fill_possible():
    prints = [{"utc_ms": 99999, "type": "sell", "price": "0.10",
               "amount": "500"}]
    a = MX.approach(_ep(lo=0, hi=1000), [_cyc(10, "0.40", "0.90")], prints)
    assert a["reaching_prints_in_the_quotes_life"] == 0
    assert a["bucket"] != MX.MAKER_POSSIBLE


def test_midpoint_drift_is_read_relative_to_the_leg_we_are_buying():
    rising = [_cyc(10, "0.30", "0.40"), _cyc(20, "0.50", "0.60")]
    a = MX.approach(_ep(leg="YES"), rising, [])
    assert a["midpoint_moved_toward_our_complete"] is True
    b = MX.approach(_ep(leg="NO", price="0.30"), rising, [])
    assert b["midpoint_moved_toward_our_complete"] is False


def test_depth_formation_is_UNKNOWN_on_the_NO_leg_not_absent():
    a = MX.approach(_ep(leg="NO", price="0.30"), [_cyc(10, "0.4", "0.5")], [])
    f = a["opposite_depth_formation"]
    assert f["observable"] is False
    assert "unknown, not absent" in f["why"]


def test_depth_formation_reports_whether_size_built():
    cycles = [_cyc(10, "0.40", "0.90", [{"price": "0.40", "qty": "5"}]),
              _cyc(20, "0.40", "0.90", [{"price": "0.40", "qty": "50"}])]
    f = MX.approach(_ep(), cycles, [])["opposite_depth_formation"]
    assert f["observable"] is True and f["built"] is True
    assert f["change"] == "45"


def test_the_census_counts_every_bucket_and_states_observability():
    rows = [MX.approach(_ep(), [_cyc(10, "0.40", "0.43")], []),
            MX.approach(_ep(), [], []),
            MX.approach(_ep(), [_cyc(10, "0.40", "0.99")], [])]
    c = MX.approach_census(rows)
    assert c["episodes"] == 3
    assert c["by_bucket"][MX.NO_BOOK] == 1
    assert c["by_bucket"][MX.WITHIN_2] == 1
    assert c["by_bucket"][MX.NEVER] == 1


# ======================================================= 2. UNDERLYING STATE

def _bars(n=30, start=1_000_000, px=80000, step=10):
    return [{"ms": D(start + i * 60000), "open": D(px + i * step),
             "high": D(px + i * step + 20), "low": D(px + i * step - 20),
             "close": D(px + i * step)} for i in range(n)]


def test_ONLY_THE_60s_VELOCITY_IS_AVAILABLE_AND_THE_REST_SAY_WHY():
    """1-minute bars cannot resolve 5s. Interpolating would be a fabrication."""
    u = MX.underlying_state(at_ms=1_000_000 + 10 * 60000, strike="80000",
                            held_leg="YES", tte_s=200, bars=_bars())
    assert u["computable"] is True
    assert u["velocity_60s"] is not None
    for h in (5, 15, 30):
        assert u["velocity_%ds" % h] is None
        assert "fabrication" in u["velocity_%ds_why" % h]


def test_the_bar_in_force_is_never_a_LATER_one():
    bars = _bars()
    u = MX.underlying_state(at_ms=1_000_000 + 5 * 60000 + 59_000,
                            strike="80000", held_leg="YES", tte_s=1,
                            bars=bars)
    assert D(u["bar_ms"]) == bars[5]["ms"]


def test_distance_from_strike_and_which_side_it_favours():
    bars = _bars(px=80000, step=0)
    up = MX.underlying_state(at_ms=1_000_000 + 60000, strike="79000",
                             held_leg="YES", tte_s=100, bars=bars)
    assert D(up["distance_from_strike"]) == D("1000")
    assert up["underlying_favours_held_side"] is True
    dn = MX.underlying_state(at_ms=1_000_000 + 60000, strike="79000",
                             held_leg="NO", tte_s=100, bars=bars)
    assert dn["underlying_favours_held_side"] is False


def test_distance_is_normalised_by_observed_volatility():
    u = MX.underlying_state(at_ms=1_000_000 + 10 * 60000, strike="80000",
                            held_leg="YES", tte_s=100, bars=_bars())
    assert u["recent_volatility_1m_range"] == "40"
    assert D(u["distance_over_volatility"]) == D("100") / D("40")


def test_a_contract_with_no_published_strike_is_refused():
    u = MX.underlying_state(at_ms=1, strike=None, held_leg="YES", tte_s=1,
                            bars=_bars())
    assert u["computable"] is False
    assert "no strike published" in u["why"]


def test_a_time_the_candles_do_not_reach_is_refused():
    u = MX.underlying_state(at_ms=1, strike="80000", held_leg="YES",
                            tte_s=1, bars=_bars())
    assert u["computable"] is False
    assert "does not reach back" in u["why"]


def test_the_contrast_states_both_cells_and_refuses_a_selector():
    rows = [{"computable": True, "par_constrained": True,
             "distance_from_strike": "100", "tte_s": 200},
            {"computable": True, "par_constrained": False,
             "distance_from_strike": "10", "tte_s": 100}]
    c = MX.underlying_contrast(rows)
    assert c["recyclable"] == 1 and c["leakage"] == 1
    assert c["smaller_cell"] == 1
    assert c["supports_a_selector"] is False
    assert c["features"]["distance_from_strike"]["difference"] == "90"


def test_SETTLEMENT_VALUE_IS_NEVER_READ_AS_A_FEATURE():
    import inspect
    src = inspect.getsource(MX.underlying_state)
    assert "settlementValue" not in src or "never" in src.lower()


# ==================================================== 3. LIQUIDITY PRESENCE

def _pr(a):
    return {"amount": str(a), "price": "0.5", "utc_ms": 1, "type": "buy"}


def _bk(qtys):
    return {"readable": True, "bids": {"levels": [
        {"price": "0.40", "qty": str(q)} for q in qtys]}}


def test_presence_anchors_on_BOTH_prints_and_displayed_book():
    p = MX.presence([_pr(10), _pr(100), _pr(263)],
                    [_bk([200, 50]), _bk([400, 10])], our_size="0.01")
    assert p["median_print"] == "100"
    assert p["median_touch_depth"] == "400"
    assert D(p["our_size_over_median_print"]) == D("0.01") / D("100")


def test_THE_VERDICT_CALLS_A_ROUNDING_ERROR_WHAT_IT_IS():
    p = MX.presence([_pr(263)], [_bk([500])], our_size="0.01")
    v = MX.presence_verdict(p)
    assert v["both_anchors_below_threshold"] is True
    assert v["verdict"] == "PLUMBING_ONLY"
    assert "does not evidence liquidity provision" in v["reading"]


def test_a_size_that_is_a_real_fraction_of_BOTH_anchors_is_meaningful():
    p = MX.presence([_pr(10)], [_bk([20])], our_size="1")
    assert MX.presence_verdict(p)["verdict"] == "MEANINGFUL"


def test_ONE_ANCHOR_ABOVE_AND_ONE_ROUNDING_ERROR_IS_MARGINAL_NOT_MEANINGFUL():
    """The binary form read as encouraging on the real data: 0.00125 of a
    median print clears a 0.001 threshold and still means one typical
    counterparty could fill us 800 times over."""
    p = MX.presence([_pr(8)], [_bk([202])], our_size="0.01")
    v = MX.presence_verdict(p)
    assert v["anchors_below_threshold"] == 1
    assert v["verdict"] == "MARGINAL"
    assert "rounding error" in v["reading"]
    assert "the RATIOS are the evidence" in v["threshold_caveat"]


def test_THE_CAUSAL_QUESTION_IS_LABELLED_UNKNOWABLE_EVERYWHERE():
    """Historical replay can say what existing flow would have done against a
    bigger quote. It cannot say what flow a bigger quote would have created."""
    p = MX.presence([_pr(10)], [_bk([20])])
    v = MX.presence_verdict(p)
    for blob in (p["causal_question"], v["causal_question"]):
        assert "NOT ANSWERABLE FROM" in blob
        assert "cannot record what a different book would have caused" in blob


def test_the_bounded_fractions_are_reference_points_not_proposals():
    p = MX.presence([_pr(100)], [_bk([1000])])
    assert set(p["bounded_presence_at_fraction"]) == set(MX.PRESENCE_FRACTIONS)
    assert D(p["bounded_presence_at_fraction"]["0.05"]["of_median_print"]) == D("5")
    assert "not proposals" in p["fractions_are"]


def test_an_empty_tape_does_not_divide_by_zero():
    p = MX.presence([], [])
    assert p["median_print"] is None
    assert p["our_size_over_median_print"] is None
    assert MX.presence_verdict(p)["verdict"] == "UNMEASURED"


def test_nothing_in_this_module_claims_a_realized_number():
    import ast
    import inspect
    banned = ("realized", "realised", "profit")
    denials = ("never", "not ", "cannot", "nothing", "unknowable")
    for node in ast.walk(ast.parse(inspect.getsource(MX))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if any(d in low for d in denials):
                continue
            for w in banned:
                assert w not in low, node.value
