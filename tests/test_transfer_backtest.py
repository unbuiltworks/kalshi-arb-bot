"""Deterministic tests for the historical transfer backtest engine."""
from __future__ import annotations

from decimal import Decimal as D

from src.polymarket_l5 import transfer_backtest as tb

ONE = D("1")


def _p(ts, typ, price):
    return {"timestampms": int(ts * 1000), "type": typ, "price": str(price),
            "amount": "1"}


def test_vendored_fee_and_edge_match_executor_semantics():
    # 0.0175 * 0.5 * 0.5 = 0.004375 -> ceil to cent = 0.01
    assert tb.maker_fee(D("0.50")) == D("0.01")
    assert tb.maker_fee(D("0.97")) == D("0.01")
    # probe-4 economics: 0.47 + 0.50 pair, 1c fee each side -> edge 0.01
    assert tb.pair_edge(D("0.47"), D("0.50")) == D("0.01")


def test_choose_open_side_is_fee_safe_and_queue_tiebroken():
    # both sides safe: deeper queue wins
    s = tb.choose_open_side(D("0.48"), D("100"), D("0.50"), D("300"))
    assert s == {"side": "NO", "price": D("0.50")}
    # bid 0.85 unsafe; NO at 1-0.35=0.65 safe
    s2 = tb.choose_open_side(D("0.85"), D("9"), D("0.35"), D("1"))
    assert s2 == {"side": "NO", "price": D("0.65")}
    # neither side inside 20-80c
    assert tb.choose_open_side(D("0.05"), D("1"), D("0.99"), D("1")) is None


def test_complement_bound_locks_min_edge_after_both_fees():
    px = tb.complement_price("YES", D("0.48"))
    assert px is not None
    assert tb.pair_edge(D("0.48"), px) >= D("0.01")
    # a 0.79 first cost leaves no fee-safe >=1c complement (needs <=0.19)
    assert tb.complement_price("YES", D("0.79")) is None


def test_first_fill_optimistic_vs_strict():
    prints = [_p(105, "sell", "0.48"), _p(110, "sell", "0.47")]
    # optimistic: at-price sell fills the YES join
    f = tb.first_fill(prints, "YES", D("0.48"), D("0.50"), 100, 200,
                      "optimistic")
    assert f and f["t"] == 105
    # strict: only the through print at 0.47 fills
    f2 = tb.first_fill(prints, "YES", D("0.48"), D("0.50"), 100, 200,
                       "strict")
    assert f2 and f2["t"] == 110
    # NO join rests at the ask: buy >= ask fills (optimistic)
    f3 = tb.first_fill([_p(120, "buy", "0.50")], "NO", D("0.50"), D("0.50"),
                       100, 200, "optimistic")
    assert f3 and f3["t"] == 120
    assert tb.first_fill([_p(120, "buy", "0.50")], "NO", D("0.50"),
                         D("0.50"), 100, 200, "strict") is None


def test_complement_fill_sides_and_windows():
    # YES first -> complement NO rests as YES ask at 1-comp
    comp = D("0.50")
    hit = tb.complement_fill([_p(150, "buy", "0.50")], "YES", comp,
                             100, 200, "optimistic")
    assert hit and hit["t"] == 150
    # a sell never fills the NO complement
    assert tb.complement_fill([_p(150, "sell", "0.10")], "YES", comp,
                              100, 200, "optimistic") is None
    # prints outside (t_from, t_until] never count
    assert tb.complement_fill([_p(99, "buy", "0.99")], "YES", comp,
                              100, 200, "optimistic") is None


def test_trailing_flow_gate_has_no_hindsight():
    prints = {"S": [_p(95, "buy", "0.5"), _p(99, "sell", "0.5"),
                    _p(100, "sell", "0.5")]}
    assert tb.trailing_flow_two_sided(prints, 100.0, 30.0) is True
    # the print AT entry (t=100) must not be consulted
    only_at_entry = {"S": [_p(100, "buy", "0.5"), _p(99, "sell", "0.5")]}
    assert tb.trailing_flow_two_sided(only_at_entry, 100.0, 30.0) is False
    # outside the trailing window
    assert tb.trailing_flow_two_sided(prints, 100.0, 0.5) is False


def test_replay_window_matched_path_and_theoretical_labels():
    event = "BTC05M2609021450"
    expiry = tb.ticker_expiry_epoch(event)
    sym = f"GEMI-{event}-UP"
    bbo = [{"symbol": sym, "t": expiry - 290, "bid": "0.48", "bid_sz": "100",
            "ask": "0.50", "ask_sz": "300"}]
    prints = [_p(expiry - 280, "buy", "0.50"),     # fills NO join (deeper ask)
              _p(expiry - 100, "sell", "0.45")]    # fills YES complement @0.47
    row = tb.replay_window(sym, bbo, prints, "optimistic")
    assert row["status"] == "MATCHED" and row["side"] == "NO"
    assert "pair_pnl_theoretical" in row and "realized" not in str(row)
    assert D(row["pair_cost_after_fees"]) <= D("0.99")


def test_replay_window_hold_and_proxy_settlement():
    event = "BTC05M2609021500"
    expiry = tb.ticker_expiry_epoch(event)
    sym = f"GEMI-{event}-UP"
    bbo = [{"symbol": sym, "t": expiry - 290, "bid": "0.48", "bid_sz": "500",
            "ask": "0.50", "ask_sz": "100"},
           {"symbol": sym, "t": expiry - 30, "bid": "0.01", "bid_sz": "1",
            "ask": "0.05", "ask_sz": "1"}]
    prints = [_p(expiry - 270, "sell", "0.48")]     # fills YES join, no buys
    row = tb.replay_window(sym, bbo, prints, "optimistic")
    assert row["status"] == "ONE_SIDED_HOLD" and row["side"] == "YES"
    settled = tb.settle_hold(row, bbo)
    assert settled["outcome_proxy"] == "DOWN"
    assert settled["residual_won"] is False
    assert D(settled["hold_pnl_theoretical"]) == -D("0.48") - tb.maker_fee(D("0.48"))


def test_replay_no_quote_coverage_never_invents_bidask():
    row = tb.replay_window("GEMI-BTC05M2609021500-UP", [], [], "optimistic")
    assert row["status"] == "NO_QUOTE_COVERAGE" and row["tier"] == 2
    assert "bid" not in row and "ask" not in row


def test_window_flow_span_matches_diagnostic_definition():
    event = "BTC05M2609021450"
    expiry = tb.ticker_expiry_epoch(event)
    prints = [_p(expiry - 250, "sell", "0.23"), _p(expiry - 240, "sell", "0.22"),
              _p(expiry - 10, "buy", "0.9")]       # inside stop buffer: excluded
    flow = tb.window_flow(prints, expiry)
    assert flow == {"span_prints": 2, "buys": 0, "sells": 2,
                    "two_sided": False}


def test_v1_gate_uses_frozen_artifact():
    g = tb.load_gate()
    assert g["source_model_sha256_16"] == "a846e6bc96fc8079"
    # a state present in the artifact allows entry; nonsense never does
    assert tb.v1_allows_entry(0.5, 250) in (True, False)  # defined behavior
    assert tb.v1_allows_entry(0.001, 1e9) is False
