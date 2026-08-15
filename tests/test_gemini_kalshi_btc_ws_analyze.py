from decimal import Decimal

from src.gemini_kalshi_btc_ws_analyze import analyze, episodes


def _row(
    ts: int,
    *,
    positive: bool = True,
    direction: str = "KALSHI_YES_GEMINI_NO",
    quantity: str = "10",
    kalshi_ask: str = "0.40",
    gemini_bid: str = "0.45",
):
    candidates = []
    if positive:
        candidates = [{
            "direction": direction,
            "quantity": quantity,
            "net_cost": str(Decimal("0.95") * Decimal(quantity)),
            "fees": "0.10",
            "locked_profit": str(Decimal("0.05") * Decimal(quantity)),
            "roi": str(Decimal("0.05") / Decimal("0.95")),
            "quote_skew_ms": 5,
        }]
    return {
        "asset": "BTC",
        "receive_ts_ms": ts,
        "gemini_receive_ts_ms": ts - 3,
        "kalshi_receive_ts_ms": ts - 8,
        "kalshi_exchange_to_receive_ms": 7,
        "gemini_yes_bid": gemini_bid,
        "gemini_yes_bid_size": quantity,
        "gemini_yes_ask": "0.46",
        "gemini_yes_ask_size": quantity,
        "kalshi_yes_bid": "0.39",
        "kalshi_yes_bid_size": quantity,
        "kalshi_yes_ask": kalshi_ask,
        "kalshi_yes_ask_size": quantity,
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


def test_repeated_events_count_one_consumable_execution():
    rows = [_row(1000), _row(1060), _row(1120), _row(1180), _row(1200, positive=False)]
    result = analyze(rows)
    assert result["entry_consumable_shadow_pnl_dollars"] == "0.50"
    assert result["survival"]["50"]["consumable_shadow_pnl_dollars"] == "0.50"
    assert result["survival"]["100"]["consumable_shadow_pnl_dollars"] == "0.50"


def test_flicker_does_not_recount_same_resting_depth():
    rows = [
        _row(1000), _row(1060), _row(1120, positive=False),
        _row(1200), _row(1260), _row(1320, positive=False),
    ]
    result = analyze(rows)
    assert result["unique_directional_positive_episodes"] == 2
    assert result["survival"]["50"]["surviving_episodes"] == 2
    assert result["survival"]["50"]["consumable_episodes"] == 1
    assert result["survival"]["50"]["consumable_contracts"] == "10"
    assert result["survival"]["50"]["consumable_shadow_pnl_dollars"] == "0.50"


def test_only_size_growth_is_new_consumable_depth_at_same_prices():
    rows = [
        _row(1000, quantity="10"), _row(1060, quantity="10"), _row(1120, positive=False),
        _row(1200, quantity="15"), _row(1260, quantity="15"), _row(1320, positive=False),
    ]
    result = analyze(rows)
    assert result["survival"]["50"]["surviving_episodes"] == 2
    assert result["survival"]["50"]["consumable_episodes"] == 2
    assert result["survival"]["50"]["consumable_contracts"] == "15"
    assert result["survival"]["50"]["consumable_shadow_pnl_dollars"] == "0.75"


def test_price_change_resets_consumable_level():
    rows = [
        _row(1000), _row(1060), _row(1120, positive=False),
        _row(1200, kalshi_ask="0.39"), _row(1260, kalshi_ask="0.39"), _row(1320, positive=False),
    ]
    result = analyze(rows)
    assert result["survival"]["50"]["consumable_contracts"] == "20"
    assert result["survival"]["50"]["consumable_shadow_pnl_dollars"] == "1.00"


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
    assert result["liquidity_method"] == "UNCHANGED_EXECUTABLE_PRICE_PAIR_COUNTS_ONLY_DISPLAYED_SIZE_GROWTH_AS_FRESH"
    assert result["settlement_status"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert result["order_entry"] is False
