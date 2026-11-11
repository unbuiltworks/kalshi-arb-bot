"""Deterministic tests for the Polymarket sports transfer backtest."""
from __future__ import annotations

from decimal import Decimal as D

from src.polymarket_l5 import sports_transfer_backtest as sb


def _t(ts, side, idx, price):
    return {"timestamp": ts, "side": side, "outcomeIndex": idx,
            "price": price, "size": 1}


def test_yes_frame_normalization():
    rows = sb.normalize_yes_frame([
        _t(10, "BUY", 0, 0.60),    # buy YES @0.60      -> buy  0.60
        _t(11, "SELL", 0, 0.58),   # sell YES @0.58     -> sell 0.58
        _t(12, "BUY", 1, 0.45),    # buy NO @0.45       -> sell 0.55
        _t(13, "SELL", 1, 0.47),   # sell NO @0.47      -> buy  0.53
    ])
    assert [(r["dir"], r["p_yes"]) for r in rows] == [
        ("buy", D("0.6")), ("sell", D("0.58")),
        ("sell", D("0.55")), ("buy", D("0.53"))]


def test_slot_matched_optimistic_and_strict_queue():
    prints = sb.normalize_yes_frame([
        _t(1030, "SELL", 0, 0.48),          # fills our YES maker @0.48
        _t(1200, "BUY", 0, 0.55),           # fills NO complement (>=0.51)
    ])
    row = sb.replay_slot(prints, 1000.0, "optimistic")
    assert row["status"] == "MATCHED" and row["side"] == "YES"
    assert row["pair_cost"] == "0.99" and row["pair_pnl_theoretical"] == "0.01"
    # strict: single sell means the queue ahead ate it; no fill for us
    row2 = sb.replay_slot(prints, 1000.0, "strict")
    assert row2["status"] == "NO_FILL_TIMEOUT"
    # strict with the queue consumed by a first sell, second fills us
    prints3 = sb.normalize_yes_frame([
        _t(1020, "SELL", 0, 0.48), _t(1040, "SELL", 0, 0.48),
        _t(1150, "BUY", 0, 0.55), _t(1160, "BUY", 0, 0.56),
    ])
    row3 = sb.replay_slot(prints3, 1000.0, "strict")
    assert row3["status"] == "MATCHED"


def test_slot_one_sided_hold_when_flow_is_one_directional():
    prints = sb.normalize_yes_frame([_t(1030, "SELL", 0, 0.48),
                                     _t(1100, "SELL", 0, 0.40)])
    row = sb.replay_slot(prints, 1000.0, "optimistic")
    assert row["status"] == "ONE_SIDED_HOLD" and row["side"] == "YES"


def test_slot_respects_first_wait_and_stop_buffer():
    # first print after the 180s wait -> no first-leg fill
    late = sb.normalize_yes_frame([_t(1000 + 200, "SELL", 0, 0.48)])
    assert sb.replay_slot(late, 1000.0, "optimistic")["status"] == "NO_FILL_TIMEOUT"
    # print inside the 25s stop buffer never counts
    buffered = sb.normalize_yes_frame([_t(1000 + 290, "SELL", 0, 0.48)])
    assert sb.replay_slot(buffered, 1000.0, "optimistic")["status"] == "NO_FILL_TIMEOUT"


def test_price_band_guard():
    row = sb.replay_slot(sb.normalize_yes_frame([_t(1030, "SELL", 0, 0.05)]),
                         1000.0, "optimistic")
    assert row["status"] == "NO_FEE_SAFE_MAKER_PRICE"


def test_slot_flow_and_replay_market_shapes():
    prints = sb.normalize_yes_frame([
        _t(1030, "SELL", 0, 0.48), _t(1200, "BUY", 0, 0.55),
        _t(1400, "SELL", 0, 0.50),
    ])
    flow = sb.slot_flow(prints, 1000.0, 1600.0)
    assert flow["slots"] == 2 and flow["two_sided"] == 1
    rows = sb.replay_market(prints, 1000.0, 1600.0, "optimistic")
    assert len(rows) == 2 and rows[0]["status"] == "MATCHED"
