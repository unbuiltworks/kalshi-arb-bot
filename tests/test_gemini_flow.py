"""Flow analysis: the metric that was wrong, and why it is now right.

The first version of this module summed `place` deltas and called the result
"unique executable liquidity". On a live BTC5m window that produced 37,805
contracts "offered" at <= 0.50 in 70 seconds against 50 actually traded, with
a cancel/place ratio of 0.994. It was measuring one quote refreshed 159 times.
These tests pin the corrected decomposition so that cannot come back.
"""
from decimal import Decimal as D

from analysis.l5.gemini_depth import flow as F
from analysis.l5.gemini_depth import report as R


def ev(t, side, price, remaining, reason, delta=None):
    return {"t": t, "type": "change", "reason": reason, "side": side,
            "price": price, "remaining": remaining,
            "delta": delta if delta is not None else remaining,
            "symbol": "S", "cohort": "BTC15m"}


def trade(t, price, amount):
    return {"t": t, "type": "trade", "reason": "", "price": price,
            "amount": amount, "symbol": "S", "cohort": "BTC15m"}


def test_a_refreshed_quote_is_not_counted_as_new_supply():
    """One 500-lot re-placed ten times is 500 of standing size, not 5000."""
    rows = [ev(0, "ask", "0.60", "500", "initial")]
    t = 1.0
    for _ in range(10):
        rows.append(ev(t, "ask", "0.60", "0", "cancel", delta="-500"))
        rows.append(ev(t + 0.01, "ask", "0.60", "500", "place", delta="500"))
        t += 1.0
    f = F.window_flow(rows, ceiling=D("0.70"))
    assert f["churn_ratio"] >= 0.9                  # almost all of it is churn
    assert D(f["net_new_eligible"]) == D("500")     # what actually accumulated
    assert f["standing_eligible_size"]["max"] == 500.0
    # the gross figure is retained ONLY to make the churn visible
    assert D(f["placed_gross_NOT_A_CAPACITY_MEASURE"]) == D("5500")


def test_genuinely_new_liquidity_is_counted():
    rows = [ev(0, "ask", "0.60", "100", "initial"),
            ev(1, "ask", "0.61", "200", "place"),
            ev(2, "ask", "0.62", "300", "place")]
    f = F.window_flow(rows, ceiling=D("0.70"))
    assert D(f["net_new_eligible"]) == D("600")
    assert f["churn_ratio"] == 0.0


def test_levels_above_the_ceiling_are_excluded_everywhere():
    rows = [ev(0, "ask", "0.60", "100", "initial"),
            ev(1, "ask", "0.90", "900", "place"),
            trade(2, "0.90", "900")]
    f = F.window_flow(rows, ceiling=D("0.70"))
    assert D(f["net_new_eligible"]) == D("100")
    assert D(f["traded_at_or_under_ceiling"]) == D("0")


def test_the_bid_side_is_not_mistaken_for_the_ask_side():
    rows = [ev(0, "bid", "0.50", "900", "initial"),
            ev(1, "ask", "0.60", "7", "initial")]
    f = F.window_flow(rows, ceiling=D("0.70"), side="ask")
    assert D(f["net_new_eligible"]) == D("7")


def test_trades_are_counted_at_or_under_the_ceiling():
    rows = [ev(0, "ask", "0.60", "100", "initial"), trade(1, "0.60", "40")]
    f = F.window_flow(rows, ceiling=D("0.70"))
    assert D(f["traded_at_or_under_ceiling"]) == D("40")


def test_the_realistic_capture_ceiling_is_resting_plus_traded():
    rows = [ev(0, "ask", "0.60", "12", "initial"), trade(1, "0.60", "8")]
    tl = F.window_flow(rows, ceiling=D("0.70"))["accumulation_trade_limited"]
    assert D(tl["realistic_capture_ceiling"]) == D("20")
    assert tl["reachable"]["20"] is True and tl["reachable"]["1"] is True


def test_a_thin_book_reports_larger_sizes_as_unreachable():
    rows = [ev(0, "ask", "0.60", "1", "initial")]
    tl = F.window_flow(rows, ceiling=D("0.70"))["accumulation_trade_limited"]
    assert tl["reachable"]["1"] is True
    assert tl["reachable"]["2"] is False and tl["reachable"]["20"] is False


def test_incremental_accumulation_is_labelled_and_never_implicit():
    rows = [ev(0, "ask", "0.60", "3", "initial"),
            ev(5, "ask", "0.60", "3", "place"),
            ev(9, "ask", "0.64", "10", "place")]
    acc = F.window_flow(rows, ceiling=D("0.70"))["accumulation_upper_bound"]
    assert "INCREMENTAL" in acc["5"]["mode"]
    assert "UPPER BOUND" in acc["5"]["mode"]
    # 3 at t=0 then 3 at t=5 already covers q=5; q=10 needs the t=9 order.
    assert acc["5"]["seconds_to_accumulate"] == 5.0
    assert acc["10"]["seconds_to_accumulate"] == 9.0


def test_a_size_never_reached_says_so_rather_than_extrapolating():
    rows = [ev(0, "ask", "0.60", "3", "initial")]
    acc = F.window_flow(rows, ceiling=D("0.70"))["accumulation_upper_bound"]
    assert acc["20"]["seconds_to_accumulate"] is None
    assert acc["20"]["reached"] == "3"


def test_capacity_is_a_function_of_the_ceiling_not_a_venue_constant():
    """The point of the owner's worked example: 1 at the touch, more behind."""
    rows = [ev(0, "ask", "0.66", "1", "initial"),
            ev(0, "ask", "0.67", "3", "initial"),
            ev(0, "ask", "0.68", "4", "initial"),
            ev(0, "ask", "0.70", "8", "initial")]
    tight = F.window_flow(rows, ceiling=D("0.66"))
    loose = F.window_flow(rows, ceiling=D("0.75"))
    assert D(tight["net_new_eligible"]) == D("1")
    assert D(loose["net_new_eligible"]) == D("16")
    assert loose["accumulation_trade_limited"]["reachable"]["10"] is True
    assert tight["accumulation_trade_limited"]["reachable"]["10"] is False


# ------------------------------------------------------------ classification

def test_every_candidate_explanation_gets_a_verdict():
    out = R.classify({}, None)
    for c in R.CLASSES:
        assert c in out and out[c]["verdict"]


def test_missing_evidence_is_unknown_not_ruled_out():
    out = R.classify({}, None)
    assert out["STALE_SNAPSHOT"]["verdict"] == "UNKNOWN"
    assert out["INCOMPLETE_BOOK"]["verdict"] == "UNKNOWN"
    assert out["UNKNOWN"]["verdict"] == "APPLIES"


def test_a_fresh_agreeing_rest_path_rules_staleness_out():
    rep = {"H3_staleness": {
        "rest_snapshot_age_s": {"p50": 0.52},
        "rest_vs_ws_best_ask_agree": {"rate": 0.99},
        "rest_vs_ws_best_qty_agree": {"rate": 1.0}}}
    assert R.classify(rep, None)["STALE_SNAPSHOT"]["verdict"] == "RULED_OUT"


def test_a_lagging_rest_path_is_reported_as_stale():
    rep = {"H3_staleness": {
        "rest_snapshot_age_s": {"p50": 9.0},
        "rest_vs_ws_best_ask_agree": {"rate": 0.40},
        "rest_vs_ws_best_qty_agree": {"rate": 0.40}}}
    assert R.classify(rep, None)["STALE_SNAPSHOT"]["verdict"] == "PRESENT"


def test_high_churn_is_reported_as_churn_dominated_movement():
    flow = {"windows": {"S": {"by_ceiling": {"0.5": {
        "churn_ratio": 0.99, "events": {"place": 500}}}}}}
    v = R.classify({}, flow)["GENUINE_BOOK_MOVEMENT"]["verdict"]
    assert v == "PRESENT_AND_DOMINATED_BY_QUOTE_CHURN"
