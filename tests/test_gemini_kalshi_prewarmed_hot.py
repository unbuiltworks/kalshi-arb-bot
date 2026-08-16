from datetime import datetime, timezone

from src.gemini_kalshi_aligned_watch import Pair
from src import gemini_kalshi_prewarmed_hot as prewarm


def _pair():
    return Pair(
        asset="BTC",
        window_end=datetime(2026, 8, 16, 21, 15, tzinfo=timezone.utc),
        gemini_symbol="GEMI-BTC15M2608162115-UP",
        kalshi_ticker="KXBTC15M-26AUG161715-15",
    )


def test_activation_ms_is_exact_window_start():
    pair = _pair()
    expected = int(datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert prewarm.activation_ms(pair) == expected


def test_preboundary_event_is_never_activated():
    pair = _pair()
    start = prewarm.activation_ms(pair)
    assert prewarm.event_is_activated(start - 1, pair) is False
    assert prewarm.event_is_activated(start, pair) is True
    assert prewarm.event_is_activated(start + 1, pair) is True


def test_next_pair_identity_targets_following_quarter_hour():
    pair = prewarm.predictive.predicted_pair(
        "BTC",
        now_ts=datetime(2026, 8, 16, 21, 0, 0, 1000, tzinfo=timezone.utc).timestamp(),
    )
    assert pair.window_end == datetime(2026, 8, 16, 21, 15, tzinfo=timezone.utc)
    assert pair.gemini_symbol == "GEMI-BTC15M2608162115-UP"
    assert pair.kalshi_ticker == "KXBTC15M-26AUG161715-15"
