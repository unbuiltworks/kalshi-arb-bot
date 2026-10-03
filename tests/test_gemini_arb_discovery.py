"""Tests for the Gemini platform-wide arbitrage scanner.

THE POINT OF THIS FILE. The scanner's headline output is a NEGATIVE: it reports
that the platform currently contains no executable arbitrage. A negative from an
instrument that cannot register a positive is worthless, so the tests below
inject arbitrage that is unambiguously there and require the scanner to find it,
price it, and size it correctly -- and then require it to decline the same setup
once fees or depth make it uneconomic.
"""
from __future__ import annotations

from decimal import Decimal as D

from src.gemini_arb_discovery import structure as ST
from src.gemini_arb_discovery.executable import (BUY_NO, BUY_YES, best_size,
                                                 evaluate, walk_leg)
from src.gemini_arb_discovery.fees import maker_fee, taker_fee


def book(bids, asks):
    return {"readable": True,
            "bids": [(D(p), D(a)) for p, a in bids],
            "asks": [(D(p), D(a)) for p, a in asks]}


# ------------------------------------------------------------------- the fees

def test_fee_table_matches_published_schedule():
    """The published per-100-contract table, to the cent."""
    assert [str(taker_fee(100, p)) for p in
            ("0.10", "0.25", "0.50", "0.75", "0.90")] == \
           ["0.63", "1.32", "1.75", "1.32", "0.63"]
    assert [str(maker_fee(100, p)) for p in
            ("0.10", "0.25", "0.50", "0.75", "0.90")] == \
           ["0.16", "0.33", "0.44", "0.33", "0.16"]


def test_fee_rounds_up_not_nearest():
    """A sub-cent theoretical fee is charged as a whole cent."""
    assert taker_fee(1, "0.50") == D("0.02")   # 0.0175 -> 0.02
    assert maker_fee(1, "0.50") == D("0.01")   # 0.004375 -> 0.01


# -------------------------------------------------------------- the book walk

def test_walk_consumes_levels_in_price_order_not_the_touch():
    """Size beyond the touch pays the next level, and the cost reflects it."""
    b = book([], [("0.10", "10"), ("0.20", "10")])
    w = walk_leg(b, BUY_YES, D("20"))
    assert w["cost"] == D("3.0")               # 10*0.10 + 10*0.20
    assert len(w["levels"]) == 2


def test_walk_refuses_more_than_the_visible_ladder():
    """Never extrapolate past displayed size."""
    assert walk_leg(book([], [("0.10", "5")]), BUY_YES, D("6")) is None


def test_buying_no_consumes_bids_at_one_minus_price():
    b = book([("0.95", "100")], [])
    w = walk_leg(b, BUY_NO, D("10"))
    assert w["cost"] == D("0.50")              # 10 * (1 - 0.95)


def test_fee_is_charged_per_level_not_once_on_the_vwap():
    """Each level is its own fill and each rounds up independently."""
    b = book([], [("0.50", "1"), ("0.51", "1")])
    w = walk_leg(b, BUY_YES, D("2"))
    assert w["fee"] == taker_fee(1, "0.50") + taker_fee(1, "0.51")


# ------------------------------------------------------- the relation engine

def _c(sym, label, stype, val, exp="2026-10-03T21:00:00.000Z", src="IDX"):
    return {"instrumentSymbol": sym, "ticker": sym, "label": label,
            "status": "active", "expiryDate": exp, "source": src,
            "strike": {"type": stype, "value": val}}


def test_ladder_is_found_across_every_monotone_strike_type():
    for stype in ("above", "over", "spread"):
        cs = [_c("A", "more than 1.5 points", stype, "1.5"),
              _c("B", "more than 2.5 points", stype, "2.5")]
        rels = ST.ladders({"ticker": "E"}, cs)
        assert [r["relation"] for r in rels] == [ST.LADDER_ABOVE], stype
    for stype in ("below", "under_or_equal"):
        cs = [_c("A", "top 5", stype, "5"), _c("B", "top 10", stype, "10")]
        assert [r["relation"] for r in ST.ladders({"ticker": "E"}, cs)] == \
               [ST.LADDER_BELOW], stype


def test_different_measures_in_one_event_are_never_one_ladder():
    """The correctness trap: two players' props are NOT monotone-related.

    If this ever regresses, the scanner starts reporting fictional arbitrage
    between independent quantities.
    """
    cs = [_c("A", "Tyler Shough 2+ Passing Touchdowns", "over", "1.5"),
          _c("B", "Michael Penix Jr. 4+ Passing Touchdowns", "over", "3.5")]
    assert ST.ladders({"ticker": "E"}, cs) == []


def test_reference_strikes_are_never_laddered():
    cs = [_c("A", "Up", "reference", "100"), _c("B", "Up", "reference", "200")]
    assert ST.ladders({"ticker": "E"}, cs) == []


def test_differing_source_breaks_the_relation_even_at_equal_expiry():
    cs = [_c("A", "x above {}", "above", "1", src="IDX_ONE"),
          _c("B", "x above {}", "above", "2", src="IDX_TWO")]
    assert [r["relation"] for r in ST.ladders({"ticker": "E"}, cs)] == [ST.UNVERIFIED]


def test_subsecond_expiry_jitter_does_not_break_one_game():
    cs = [_c("A", "team scores more than {} points", "over", "1.5",
             exp="2026-10-28T22:17:29.570Z", src=None),
          _c("B", "team scores more than {} points", "over", "2.5",
             exp="2026-10-28T22:17:31.705Z", src=None)]
    assert [r["relation"] for r in ST.ladders({"ticker": "E"}, cs)] == [ST.LADDER_ABOVE]


def test_weather_bucket_set_verifies_as_an_exhaustive_partition():
    cs = [_c("LO", "69°F or below", "below", "69"),
          {"instrumentSymbol": "M1", "ticker": "M1", "label": "70°F to 71°F",
           "status": "active", "expiryDate": "2026-10-03T21:00:00.000Z",
           "source": "IDX", "strike": None},
          _c("HI", "72°F or above", "above", "72")]
    assert ST.verify_partition(cs, step=D("1"))["exhaustive"] is True


def test_a_gap_in_the_cover_is_not_a_partition():
    cs = [_c("LO", "69°F or below", "below", "69"),
          _c("HI", "72°F or above", "above", "72")]      # 70, 71 uncovered
    assert ST.verify_partition(cs, step=D("1"))["exhaustive"] is False


def test_nested_above_ladder_is_not_mistaken_for_a_partition():
    """Crypto/commodity ladders overlap; summing their asks would be nonsense."""
    cs = [_c("A", "$89 or above", "above", "89"),
          _c("B", "$90 or above", "above", "90")]
    assert ST.verify_partition(cs, step=D("1"))["exhaustive"] is False


# --------------------------------------------------- the economics, end to end

def test_monotonicity_violation_is_priced_and_sized():
    """Buy YES(low strike) at 0.40, buy NO(high strike) at 1-0.50.

    Cost per unit 0.40 + 0.50 = 0.90 against a guaranteed payoff of 1.00, so
    0.10 of gross edge per unit before fees.
    """
    legs = [{"symbol": "LOW", "action": BUY_YES,
             "book": book([], [("0.40", "100")])},
            {"symbol": "HIGH", "action": BUY_NO,
             "book": book([("0.50", "100")], [])}]
    ev = evaluate(legs, D("100"))
    assert ev["gross_locked_payoff"] == "100"
    assert D(ev["capital_required"]) == D("90.0")
    expected_fees = taker_fee(100, "0.40") + taker_fee(100, "0.50")
    assert D(ev["fees"]) == expected_fees
    assert D(ev["net_locked_profit"]) == D("10.0") - expected_fees
    assert D(ev["net_locked_profit"]) > 0


def test_thin_edge_is_refused_once_fees_are_charged():
    """A one-cent gross edge does not survive two taker legs near 0.50."""
    legs = [{"symbol": "LOW", "action": BUY_YES,
             "book": book([], [("0.49", "100")])},
            {"symbol": "HIGH", "action": BUY_NO,
             "book": book([("0.50", "100")], [])}]
    ev = evaluate(legs, D("100"))
    assert D(ev["net_locked_profit"]) <= 0


def test_best_size_stops_where_the_edge_stops_and_reports_capacity():
    """Edge exists for the first 10 only; depth beyond that is unprofitable."""
    legs = [{"symbol": "LOW", "action": BUY_YES,
             "book": book([], [("0.30", "10"), ("0.80", "1000")])},
            {"symbol": "HIGH", "action": BUY_NO,
             "book": book([("0.60", "10"), ("0.10", "1000")], [])}]
    res = best_size(legs, increment=D("1"), maximum=D("50"))
    assert res["best"] is not None
    assert D(res["best"]["qty"]) <= D("10")
    assert D(res["best"]["net_locked_profit"]) > 0


def test_exhaustive_partition_underpricing_is_profitable():
    """Three mutually exclusive, exhaustive outcomes at 0.30 each pay 1.00."""
    legs = [{"symbol": s, "action": BUY_YES,
             "book": book([], [("0.30", "100")])} for s in ("A", "B", "C")]
    ev = evaluate(legs, D("100"))
    assert D(ev["capital_required"]) == D("90.0")
    assert D(ev["net_locked_profit"]) > 0


def test_a_normal_market_yields_nothing():
    """The ordinary case: asks sum above 1. Must produce no size at all."""
    legs = [{"symbol": s, "action": BUY_YES,
             "book": book([], [("0.40", "100")])} for s in ("A", "B", "C")]
    assert best_size(legs, increment=D("1"), maximum=D("50"))["best"] is None


# ----------------------------------------------- the scan path, end to end

def test_scan_promotes_a_crossed_candidate_to_depth_executable(monkeypatch):
    """A candidate whose real books cross must come back DEPTH_EXECUTABLE.

    This exercises the actual `depth_confirm` path -- the one the platform scan
    runs -- rather than the economics in isolation, so a wiring mistake between
    candidate construction and book evaluation cannot hide behind green unit
    tests.
    """
    from src.gemini_arb_discovery import scan as SC

    fake = {"LOW": book([], [("0.40", "100")]),
            "HIGH": book([("0.50", "100")], [])}
    monkeypatch.setattr(SC.BK, "fetch_book",
                        lambda sym, **kw: {**fake[sym], "symbol": sym})

    cand = {"kind": ST.LADDER_ABOVE, "event": "E", "event_title": "t",
            "category": "Test", "why_guaranteed": "test",
            "quoted_edge_per_unit": "0.10", "increment": "1", "minimum": "1",
            "legs": [{"symbol": "LOW", "action": BUY_YES},
                     {"symbol": "HIGH", "action": BUY_NO}]}
    out = SC.depth_confirm(cand, max_qty=D("100"))
    assert out["stage"] == "DEPTH_EXECUTABLE"
    assert D(out["economics"]["net_locked_profit"]) > 0
    # legging risk is always stated, never silently omitted
    assert out["legging"]["legs"] == 2
    assert "UNVERIFIED" in out["legging"]["simultaneously_submittable"]


def test_scan_reports_an_unreadable_book_instead_of_assuming_no_edge(monkeypatch):
    """A venue we could not read is 'unknown', never 'no arbitrage here'."""
    from src.gemini_arb_discovery import scan as SC
    monkeypatch.setattr(SC.BK, "fetch_book",
                        lambda sym, **kw: {"readable": False, "symbol": sym,
                                           "err": "timeout"})
    cand = {"kind": ST.LADDER_ABOVE, "event": "E", "event_title": "t",
            "category": "Test", "why_guaranteed": "test",
            "quoted_edge_per_unit": "0.10", "increment": "1", "minimum": "1",
            "legs": [{"symbol": "LOW", "action": BUY_YES}]}
    out = SC.depth_confirm(cand, max_qty=D("10"))
    assert out["stage"] == "QUOTED"
    assert "unreadable" in out["depth_verdict"]


# ------------------------------------------------- exact breakpoint sizing

def test_breakpoint_sizing_lands_exactly_on_the_knee():
    """Edge covers the first 10; the optimum is 10, not 9 and not 11."""
    legs = [{"symbol": "LOW", "action": BUY_YES,
             "book": book([], [("0.30", "10"), ("0.80", "1000")])},
            {"symbol": "HIGH", "action": BUY_NO,
             "book": book([("0.60", "10"), ("0.10", "1000")], [])}]
    res = best_size(legs, increment=D("0.01"), maximum=D("500"))
    assert res["best"]["qty"] == "10"


def test_ceil_cent_fee_floors_the_minimum_viable_size():
    """Every contract quotes quantityIncrement 0.01, and that size cannot pay.

    At 0.05 contracts a 30-cent gross edge per unit is 0.0035 of profit, while
    the venue rounds EACH leg's fee up to a whole cent -- $0.02 on two legs. So
    the venue's own minimum order size is nowhere near the minimum PROFITABLE
    size, and the floor is set by fee rounding rather than by the edge.
    """
    legs = [{"symbol": "LOW", "action": BUY_YES,
             "book": book([], [("0.30", "0.05")])},
            {"symbol": "HIGH", "action": BUY_NO,
             "book": book([("0.60", "0.05")], [])}]
    assert best_size(legs, increment=D("0.01"), maximum=D("500"))["best"] is None


def test_same_edge_becomes_viable_once_size_clears_the_fee_quantum():
    """The identical 0.30 per-unit edge at 10 contracts clears both cents."""
    legs = [{"symbol": "LOW", "action": BUY_YES,
             "book": book([], [("0.30", "10")])},
            {"symbol": "HIGH", "action": BUY_NO,
             "book": book([("0.60", "10")], [])}]
    res = best_size(legs, increment=D("0.01"), maximum=D("500"))
    assert res["best"] is not None
    assert D(res["best"]["qty"]) == D("10")
    assert D(res["best"]["net_locked_profit"]) > 0


def test_sizing_never_exceeds_the_cap():
    legs = [{"symbol": "A", "action": BUY_YES,
             "book": book([], [("0.10", "10000")])},
            {"symbol": "B", "action": BUY_NO,
             "book": book([("0.95", "10000")], [])}]
    res = best_size(legs, increment=D("1"), maximum=D("25"))
    assert D(res["best"]["qty"]) <= D("25")
