from decimal import Decimal

from src import sports_kp_live_combo_supervisor_v41 as v41

D = Decimal


def setup_function():
    v41.v8._MARKETS.clear()
    v41.v8._QUOTES.clear()


def test_scalar_minimal_no_cover_identity():
    samples = [
        (D("0"), D("0.7"), D("0.9")),
        (D("0.25"), D("0.4"), D("0.8")),
        (D("0.5"), D("0.5"), D("0.5")),
        (D("1"), D("1"), D("1")),
    ]
    for x, y, z in samples:
        product = x * y * z
        for selected in (x, y, z):
            assert D("1") - product + selected >= D("1")


def test_minimal_inverse_uses_only_best_single_selected_leg(monkeypatch):
    monkeypatch.setenv("MVE_LIVE_MIN_LOCKED", "0.03")
    ticker = "MVE-X"
    v41.v8._QUOTES[ticker] = [
        {"id": "q1", "status": "open", "yes_bid_dollars": "0.96", "updated_ts": "now"}
    ]
    v41.v8._MARKETS.update({
        "A": {"yes_ask_dollars": "0.90", "yes_ask_size_fp": "5"},
        "B": {"yes_ask_dollars": "0.91", "yes_ask_size_fp": "5"},
        "C": {"yes_ask_dollars": "0.95", "yes_ask_size_fp": "5"},
    })
    row = {
        "ticker": ticker,
        "legs": [
            {"market_ticker": "A", "side": "yes"},
            {"market_ticker": "B", "side": "yes"},
            {"market_ticker": "C", "side": "yes"},
        ],
    }
    out = v41._score_minimal_inverse(row)
    assert out is not None
    assert out["direction"] == "COMBO_NO_PLUS_ONE_SELECTED_LEG"
    assert out["component_count"] == 1
    assert len(out["components"]) == 1
    assert out["components"][0]["market_ticker"] == "A"
    # Combo NO costs 0.04 + 0.01 fee; selected A costs 0.90 + 0.01 fee.
    assert D(out["all_in_cost_conservative"]) == D("0.96")
    assert D(out["locked_profit_conservative"]) == D("0.04")
    assert out["classification"] == "SHADOW_EXECUTABLE_MINIMAL_NO_COVER"
    assert out["live_execution_enabled"] is False
    assert out["order_entry"] is False


def test_minimal_inverse_fails_closed_without_q1_component_depth():
    ticker = "MVE-Y"
    v41.v8._QUOTES[ticker] = [
        {"id": "q2", "status": "open", "yes_bid_dollars": "0.99", "updated_ts": "now"}
    ]
    v41.v8._MARKETS["A"] = {"yes_ask_dollars": "0.90", "yes_ask_size_fp": "0.5"}
    out = v41._score_minimal_inverse({
        "ticker": ticker,
        "legs": [{"market_ticker": "A", "side": "yes"}],
    })
    assert out is None
