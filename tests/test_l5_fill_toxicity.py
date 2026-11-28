"""Feature construction and no-lookahead guarantees of the toxicity dataset."""

from __future__ import annotations

from src.polymarket_l5.fill_toxicity import (
    RECENT_START_TS,
    build_dataset,
    bucket_report,
    stable_sign,
)

W = "0xwin"
T0 = RECENT_START_TS + 1000


def fill(ts, outcome, price, shares, tx="0x1"):
    return {
        "window_id": W,
        "timestamp": str(ts),
        "asset": "BTC",
        "window_minutes": "5",
        "side": "BUY",
        "outcome": outcome,
        "price": str(price),
        "shares": str(shares),
        "dollars": str(price * shares),
        "seconds_from_window_start": str(ts - T0),
        "transaction_hash": tx,
        "condition_id": W,
        "token_id": "tok",
    }


def test_pnl_attribution_is_settlement_additive():
    fills = [fill(T0 + 10, "Up", 0.4, 10), fill(T0 + 20, "Down", 0.5, 10)]
    ex = build_dataset(fills, set(), {W: "Up"})
    assert round(ex[0]["pnl"], 6) == 6.0      # 10*1 - 4
    assert round(ex[1]["pnl"], 6) == -5.0     # lost side: -5
    assert sum(e["pnl"] for e in ex) == 1.0   # matches derived window pnl


def test_features_use_only_prior_state():
    fills = [
        fill(T0 + 10, "Up", 0.40, 10),
        fill(T0 + 11, "Up", 0.40, 5),   # same price within burst window
        fill(T0 + 30, "Down", 0.55, 15),
    ]
    ex = build_dataset(fills, set(), {W: "Up"})
    # first fill sees empty state
    assert ex[0]["imbalance_signed_toward_fill"] == 0.0
    assert ex[0]["burst_count"] == 0
    assert ex[0]["streak_len_same_side"] == 0
    assert ex[0]["price_drift_same_side"] is None
    # second fill sees the first
    assert ex[1]["burst_count"] == 1
    assert ex[1]["streak_len_same_side"] == 1
    assert ex[1]["imbalance_signed_toward_fill"] == 1.0   # all inventory is Up, fill is Up
    # third fill: buying Down against an Up-heavy book reduces residual
    assert ex[2]["reduces_residual"] is True
    assert ex[2]["imbalance_signed_toward_fill"] == -1.0  # imbalance away from Down fill
    assert ex[2]["streak_len_same_side"] == 0


def test_unresolved_windows_are_dropped():
    ex = build_dataset([fill(T0 + 10, "Up", 0.4, 10)], set(), {W: ""})
    assert ex == []


def test_taker_fingerprints_split_maker_label():
    from src.polymarket_l5.fill_toxicity import _fingerprint_of_row

    f1, f2 = fill(T0 + 10, "Up", 0.4, 10, tx="0xa"), fill(T0 + 20, "Up", 0.5, 10, tx="0xb")
    taker_fps = {_fingerprint_of_row(f2)}
    ex = build_dataset([f1, f2], taker_fps, {W: "Up"})
    assert ex[0]["is_maker"] is True
    assert ex[1]["is_maker"] is False


def test_bucket_report_and_stability_verdicts():
    fills = [fill(T0 + 10 + i, "Up", 0.40, 10, tx=f"0x{i}") for i in range(10)]
    ex = build_dataset(fills, set(), {W: "Up"})
    report = bucket_report(ex, "price", ((0.0, 0.5), (0.5, 1.01)))
    verdicts = stable_sign(report)
    assert verdicts["0..0.5"] == "stable_positive"   # all winning fills
    assert "0.5..1.01" not in report or verdicts.get("0.5..1.01") == "insufficient"
