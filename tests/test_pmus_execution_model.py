"""R4/R5/R6 as regression cases, plus the mechanics they taught us."""
from decimal import Decimal as D

from src import pmus_execution_model as M


# ------------------------------------------------- single-book semantics ---
def test_down_buy_is_a_wire_sell_at_one_minus_own_price():
    """R4's venue record: own 0.73 DOWN == wire 0.27 SELL / BUY_SHORT."""
    assert M.own_price_to_wire(False, "0.73") == D("0.27")
    assert M.own_price_to_wire(True, "0.26") == D("0.26")


def test_up_buy_crosses_when_at_or_above_the_ask():
    assert M.crosses_on_arrival(True, "0.44", 0.43, 0.44) is True
    assert M.crosses_on_arrival(True, "0.43", 0.43, 0.44) is False


def test_down_buy_crosses_when_wire_sell_is_at_or_below_the_bid():
    # own 0.54 DOWN -> wire 0.46; bid 0.46 -> crosses
    assert M.crosses_on_arrival(False, "0.54", 0.46, 0.47) is True
    assert M.crosses_on_arrival(False, "0.54", 0.45, 0.46) is False


def test_a_one_sided_arrival_book_is_unknown_not_accepted():
    assert M.crosses_on_arrival(True, "0.40", None, 0.50) is None
    v = M.post_only_verdict(True, "0.40", decision_bid=0.4, decision_ask=0.5,
                            arrival_bid=None, arrival_ask=None)
    assert v["verdict"] == "UNKNOWN"


# ------------------------------------------- the four R5 rejections, exactly ---
def test_r5_order_5_rejected_because_the_market_fell_before_arrival():
    """UP 0.43: ask 0.44 at decision, 0.40 at arrival -> crosses on arrival."""
    v = M.post_only_verdict(True, "0.43",
                            decision_bid=0.43, decision_ask=0.44,
                            arrival_bid=0.39, arrival_ask=0.40)
    assert v["verdict"] == "REJECT"
    assert v["crossed_at_decision"] is False
    assert v["staleness_changed_the_outcome"] is True


def test_r5_order_7_down_rejected_on_arrival():
    """DOWN own 0.54 -> wire 0.46; bid moved 0.45 -> 0.46."""
    v = M.post_only_verdict(False, "0.54",
                            decision_bid=0.45, decision_ask=0.46,
                            arrival_bid=0.46, arrival_ask=0.47)
    assert v["verdict"] == "REJECT"
    assert v["staleness_changed_the_outcome"] is True


def test_r5_order_1_accepted_rests_as_maker():
    v = M.post_only_verdict(True, "0.50",
                            decision_bid=0.50, decision_ask=0.51,
                            arrival_bid=0.50, arrival_ask=0.51)
    assert v["verdict"] == "ACCEPT" and v["why"] == "RESTS_AS_MAKER"
    assert v["staleness_changed_the_outcome"] is False


# ------------------------------------------------ full-inventory settlement ---
def test_r4_settles_to_its_venue_confirmed_loss():
    c = M.LIVE_CASES["R4"]
    r = M.residual_settlement_pnl(c["up_qty"], c["down_qty"], c["cost_usd"],
                                  c["settlement_px_long"])
    assert r["long_side_won"] is False
    assert r["gross_return_usd"] == "0"          # UP expired worthless
    assert r["trading_pnl_usd"] == "-2.60"


def test_r5_settles_to_minus_8_30_before_commissions():
    """Cash anchor said -8.06; commissions were a +0.24 credit."""
    c = M.LIVE_CASES["R5"]
    r = M.residual_settlement_pnl(c["up_qty"], c["down_qty"], c["cost_usd"],
                                  c["settlement_px_long"])
    assert r["long_side_won"] is True
    assert r["gross_return_usd"] == "30"
    assert r["trading_pnl_usd"] == "-8.30"
    assert r["matched_pairs"] == "30"
    assert r["residual_shares_signed"] == "-20"


def test_r6_settles_to_plus_3_50_and_a_sub_dollar_pair_cost():
    c = M.LIVE_CASES["R6"]
    r = M.residual_settlement_pnl(c["up_qty"], c["down_qty"], c["cost_usd"],
                                  c["settlement_px_long"])
    assert r["trading_pnl_usd"] == "3.50"
    assert r["matched_pairs"] == "40"
    assert r["residual_shares_signed"] == "0"
    assert r["pair_cost_per_matched"] == "0.9125"


def test_undecidable_settlement_is_not_scored_as_zero():
    r = M.residual_settlement_pnl("10", "0", "2.60", None)
    assert r["status"] == "UNDECIDABLE"


# ------------------------------------------------------- the frozen record ---
def test_the_live_cases_record_the_rejection_rate_we_must_model():
    tot = sum(c["orders_submitted"] for c in M.LIVE_CASES.values())
    rej = sum(c["rejected"] for c in M.LIVE_CASES.values())
    assert (tot, rej) == (31, 5)


def test_r4_latencies_are_frozen_as_observed():
    c = M.LIVE_CASES["R4"]
    assert c["fill_latency_s"] == 10.701
    assert c["reject_latency_s"] == 0.094


def test_a_locked_or_crossed_arrival_book_is_unknowable_not_a_rejection():
    """Inventing rejections from degenerate snapshots would be the mirror
    image of the defect this module fixes."""
    assert M.crosses_on_arrival(True, "0.40", 0.40, 0.40) is None
    assert M.crosses_on_arrival(True, "0.40", 0.45, 0.41) is None
    v = M.post_only_verdict(True, "0.40", decision_bid=0.40,
                            decision_ask=0.60, arrival_bid=0.40,
                            arrival_ask=0.40)
    assert v["verdict"] == "UNKNOWN"
