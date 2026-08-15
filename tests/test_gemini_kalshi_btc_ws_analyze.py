from decimal import Decimal

from src.gemini_kalshi_btc_ws_analyze import analyze, episodes


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
        "receive_ts_ms": ts,
        "gemini_receive_ts_ms": ts - 3,
        "kalshi_receive_ts_ms": ts - 8,
        "kalshi_exchange_to_receive_ms": 7,
        "both_taker_fee_candidates": candidates,
        "read_only": True,
        "order_entry": False,
    }


def test_requires_new_positive_event_at_or_after_threshold():
    rows = [_row(1000), _row(1040), _row(1060), _row(1110), _row(1300, positive=False)]
    result = analyze(rows)
    assert result["unique_directional_positive_episodes"] == 1
    assert result["survival"]["50"]["surviving_episodes"] == 1
    assert result["survival"]["100"]["surviving_episodes"] == 1
    assert result["survival"]["250"]["surviving_episodes"] == 0
    assert result["survival"]["500"]["surviving_episodes"] == 0
    assert result["survival_method"] == "REQUIRES_NEW_POSITIVE_EVENT_AT_OR_AFTER_THRESHOLD"


def test_does_not_infer_survival_across_silent_gap():
    rows = [_row(1000), _row(1600, positive=False)]
    result = analyze(rows)
    for threshold in ("50", "100", "250", "500"):
        assert result["survival"][threshold]["surviving_episodes"] == 0


def test_repeated_events_count_one_execution_per_episode():
    rows = [_row(1000), _row(1060), _row(1120), _row(1180), _row(1200, positive=False)]
    result = analyze(rows)
    assert result["entry_one_execution_per_episode_shadow_pnl_dollars"] == "0.50"
    assert result["survival"]["50"]["one_execution_per_surviving_episode_shadow_pnl_dollars"] == "0.50"
    assert result["survival"]["100"]["one_execution_per_surviving_episode_shadow_pnl_dollars"] == "0.50"


def test_direction_change_is_separate_episode():
    rows = [
        _row(1000, direction="KALSHI_YES_GEMINI_NO"),
        _row(1060, direction="GEMINI_YES_KALSHI_NO"),
        _row(1120, positive=False),
    ]
    eps = episodes(rows)
    assert len(eps) == 2
    assert {ep.direction for ep in eps} == {"KALSHI_YES_GEMINI_NO", "GEMINI_YES_KALSHI_NO"}


def test_latency_and_skew_metrics_are_reported():
    result = analyze([_row(1000), _row(1060), _row(1120, positive=False)])
    assert result["quote_skew_median_ms"] == 5
    assert result["kalshi_exchange_to_receive_median_ms"] == 7
    assert result["event_interval_median_ms"] == 60
    assert result["settlement_status"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert result["order_entry"] is False
