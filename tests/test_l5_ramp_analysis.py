"""Deterministic checks for the ramp re-analysis helpers (issue #167
comment 5520342503). Research-only module; these pin the sequential
feature math on synthetic windows."""
from __future__ import annotations

from src.polymarket_l5 import ramp_analysis as ra


def _row(ts, seq, outcome, side, price, qty, resid, matched="0",
         pc="", day="2026-04-09"):
    return {"window_id": "w", "market_slug": "m", "asset": "BTC",
            "duration_minutes": "15", "trade_seq": str(seq),
            "timestamp": str(ts), "datetime_utc": f"{day}T10:00:00Z",
            "seconds_to_settlement": "600", "outcome": outcome,
            "side": side, "price": str(price), "quantity": str(qty),
            "dollars": str(float(price) * float(qty)),
            "maker_taker": "unknown", "residual_qty_signed": str(resid),
            "residual_side": "Up" if float(resid) > 0 else "",
            "matched_qty": matched, "matched_pair_unit_cost": pc,
            "settlement_outcome": "Up", "source": "test"}


def test_window_features_runs_transitions_and_flip_gaps():
    rows = [
        _row(100, 1, "Up", "BUY", 0.50, 10, 10),            # open
        _row(110, 2, "Up", "BUY", 0.505, 10, 20),           # extend (nearby)
        _row(140, 3, "Up", "BUY", 0.52, 10, 30),            # extend
        _row(200, 4, "Down", "BUY", 0.46, 10, 20, "10", "0.96"),  # complete
    ]
    w = ra.window_features(rows)
    assert w["epoch"] == "ramp" and w["n_buys"] == 4
    assert w["runs"] == [3, 1]                    # same-side run then flip
    assert w["trans"][("extend", "extend")] == 1  # seq2->seq3
    assert w["trans"][("extend", "complete")] == 1
    assert w["flip_gaps"] == [60.0]               # 140 -> 200
    assert 0 < w["nearby_frac"] <= 0.5            # one nearby of two pairs
    assert w["peak_resid"] == 30.0
    assert w["pc_final"] == 0.96
    assert w["complete_prices"] == [0.46]


def test_epoch_boundaries_match_the_directive():
    assert ra.epoch_of("2026-03-25") == "early"
    assert ra.epoch_of("2026-03-26") == "transition"
    assert ra.epoch_of("2026-04-05") == "developing"
    assert ra.epoch_of("2026-04-09") == "ramp"
    assert ra.epoch_of("2026-04-20") == "mature_apr"
    assert ra.epoch_of("2026-08-30") == "mature_aug"


def test_changepoint_finds_the_injected_mean_shift():
    daily = {f"2026-04-{d:02d}": {"fills": 10 + (d % 2)} for d in range(1, 8)}
    daily.update({f"2026-04-{d:02d}": {"fills": 100 + (d % 2)}
                  for d in range(8, 15)})
    cp = ra.changepoint(daily, "fills")
    assert cp["first_new_day"] == "2026-04-08"
    assert cp["t_stat"] > 10
