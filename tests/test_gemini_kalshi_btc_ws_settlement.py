from datetime import datetime, timezone
from decimal import Decimal
import json

from src.gemini_kalshi_btc_ws_settlement import load_episodes_stream, settlement_adjusted
from src.gemini_kalshi_settlement_agreement import AgreementRow


def _row(ts: int, *, positive: bool = True, direction: str = "KALSHI_YES_GEMINI_NO"):
    candidates = []
    if positive:
        candidates = [{
            "direction": direction,
            "quantity": "10",
            "net_cost": "9.50",
            "fees": "0.10",
            "locked_profit": "0.50",
            "roi": str(Decimal("0.50") / Decimal("9.50")),
            "quote_skew_ms": 5,
        }]
    return {
        "asset": "BTC",
        "window_end_utc": "2026-08-15T12:00:00+00:00",
        "gemini_symbol": "GEMI-BTC15M-W1-UP",
        "kalshi_ticker": "KXBTC15M-W1",
        "receive_ts_ms": ts,
        "gemini_receive_ts_ms": ts - 3,
        "kalshi_receive_ts_ms": ts - 8,
        "kalshi_exchange_to_receive_ms": 7,
        "gemini_yes_bid": "0.45",
        "gemini_yes_bid_size": "10",
        "gemini_yes_ask": "0.46",
        "gemini_yes_ask_size": "10",
        "kalshi_yes_bid": "0.39",
        "kalshi_yes_bid_size": "10",
        "kalshi_yes_ask": "0.40",
        "kalshi_yes_ask_size": "10",
        "both_taker_fee_candidates": candidates,
        "read_only": True,
        "order_entry": False,
    }


def _agreement(*, gemini: str, kalshi: str) -> AgreementRow:
    return AgreementRow(
        asset="BTC",
        expiry=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        gemini_ticker="GEMI-BTC15M-W1-UP",
        kalshi_ticker="KXBTC15M-W1",
        gemini_outcome=gemini,
        kalshi_outcome=kalshi,
        agrees=(gemini == kalshi),
        gemini_strike=100.0,
        gemini_settlement=100.01,
        gemini_margin_bps=1.0,
    )


def test_agreement_preserves_apparent_locked_profit():
    rows = [_row(1000), _row(1060), _row(1120, positive=False)]
    result = settlement_adjusted(rows, [_agreement(gemini="yes", kalshi="yes")])
    assert result["entry"]["apparent_locked_profit_dollars"] == "0.50"
    assert result["entry"]["settlement_adjusted_pnl_dollars"] == "0.50"
    assert result["entry"]["basis_effect_dollars"] == "0.00"


def test_adverse_mismatch_can_turn_lock_into_full_cost_loss():
    rows = [_row(1000), _row(1060), _row(1120, positive=False)]
    result = settlement_adjusted(rows, [_agreement(gemini="yes", kalshi="no")])
    entry = result["entry"]
    assert entry["apparent_locked_profit_dollars"] == "0.50"
    assert entry["settlement_adjusted_pnl_dollars"] == "-9.50"
    assert entry["basis_effect_dollars"] == "-10.00"
    assert entry["adverse_mismatch_consumable_episodes"] == 1


def test_favorable_mismatch_can_pay_both_legs():
    rows = [_row(1000, direction="GEMINI_YES_KALSHI_NO"), _row(1060, direction="GEMINI_YES_KALSHI_NO"), _row(1120, positive=False)]
    result = settlement_adjusted(rows, [_agreement(gemini="yes", kalshi="no")])
    entry = result["entry"]
    assert entry["settlement_adjusted_pnl_dollars"] == "10.50"
    assert entry["basis_effect_dollars"] == "10.00"
    assert entry["favorable_mismatch_consumable_episodes"] == 1


def test_unsettled_episode_is_excluded_not_assumed_safe():
    result = settlement_adjusted([_row(1000), _row(1060)], [])
    assert result["entry"]["unresolved_episode_candidates"] == 1
    assert result["entry"]["consumable_contracts"] == "0"
    assert result["entry"]["settlement_adjusted_pnl_dollars"] == "0"


def test_latency_scenario_uses_same_actual_settlement_accounting():
    rows = [_row(1000), _row(1060), _row(1120, positive=False)]
    result = settlement_adjusted(rows, [_agreement(gemini="yes", kalshi="no")])
    assert result["survival"]["50"]["settlement_adjusted_pnl_dollars"] == "-9.50"
    assert result["survival"]["100"]["settlement_adjusted_pnl_dollars"] == "0"
    assert result["classification"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert result["order_entry"] is False


def test_stream_loader_discards_non_positive_capture_rows(tmp_path):
    path = tmp_path / "capture.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for ts in range(1_000, 11_000):
            row = _row(ts, positive=(ts in {5_000, 5_006, 5_012}))
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    eps = load_episodes_stream([path])
    assert len(eps) == 3  # each positive tick is separated by a non-positive event
    assert sum(len(ep.observations) for ep in eps) == 3
