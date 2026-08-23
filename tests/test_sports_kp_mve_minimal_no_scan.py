from decimal import Decimal

from src import sports_kp_mve_minimal_no_scan as m

D = Decimal


def test_selected_yes_quote_requires_q1_depth():
    q = m._selected_quote({"yes_ask_dollars": "0.40", "yes_ask_size_fp": "2"}, "yes")
    assert q is not None
    assert q["price"] == D("0.40")
    assert q["depth"] == D("2")
    assert q["source"] == "yes_ask"
    assert q["all_in"] == D("0.42")
    assert m._selected_quote({"yes_ask_dollars": "0.40", "yes_ask_size_fp": "0.5"}, "yes") is None


def test_selected_no_quote_can_derive_from_yes_bid():
    q = m._selected_quote({"yes_bid_dollars": "0.72", "yes_bid_size_fp": "3"}, "no")
    assert q is not None
    assert q["price"] == D("0.28")
    assert q["depth"] == D("3")
    assert q["source"] == "derived_from_yes_bid"


def test_public_combo_no_can_derive_from_yes_bid_with_q1_depth():
    q = m._public_combo_no({"yes_bid_dollars": "0.94", "yes_bid_size_fp": "4"})
    assert q is not None
    assert q["price"] == D("0.06")
    assert q["depth"] == D("4")
    assert q["source"] == "derived_from_yes_bid"
    assert m._public_combo_no({"yes_bid_dollars": "0.94", "yes_bid_size_fp": "0.2"}) is None


def test_rfq_combo_no_uses_highest_positive_yes_bid():
    best, stats = m._best_rfq_combo_no([
        {"status": "open", "yes_bid_dollars": "0", "no_bid_dollars": "0.20"},
        {"status": "open", "yes_bid_dollars": "0.91", "no_bid_dollars": "0"},
        {"status": "open", "yes_bid_dollars": "0.95", "no_bid_dollars": "0.01"},
    ])
    assert stats == {"quotes": 3, "positive_yes_bid": 2, "positive_no_bid": 2}
    assert best is not None
    assert best["price"] == D("0.05")
    assert best["source"] == "rfq_yes_bid_complement"


def test_score_marks_only_shadow_and_never_order_entry():
    component = {
        "market_ticker": "LEG-A",
        "selected_side": "yes",
        "price": D("0.90"),
        "depth": D("5"),
        "fee": D("0.01"),
        "all_in": D("0.91"),
        "source": "yes_ask",
    }
    combo_no = {
        "price": D("0.04"),
        "depth": D("2"),
        "fee": D("0.01"),
        "all_in": D("0.05"),
        "source": "no_ask",
    }
    out = m._score(
        ticker="MVE-X",
        title="test",
        close_time="2026-08-23T03:00:00+00:00",
        leg_count=2,
        component=component,
        combo_no=combo_no,
        price_source="PUBLIC_BOOK",
    )
    assert D(out["all_in_cost_conservative"]) == D("0.96")
    assert D(out["locked_profit_conservative"]) == D("0.04")
    assert out["classification"] == "SHADOW_EXECUTABLE_MINIMAL_NO_COVER"
    assert out["live_execution_enabled"] is False
    assert out["market_creation"] is False
    assert out["quote_acceptance"] is False
    assert out["order_entry"] is False
    assert out["realized"] is False
