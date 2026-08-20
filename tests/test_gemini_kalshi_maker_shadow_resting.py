import asyncio

from src import gemini_kalshi_maker_shadow as maker
from src import gemini_kalshi_maker_shadow_resting as resting


def test_resting_liveness_install_only_replaces_pair_capture():
    original_capture = maker._capture_pair
    original_process = maker._process_trade
    original_enqueue = maker._enqueue
    try:
        resting.install(500)
        assert maker._capture_pair is not original_capture
        assert maker._process_trade is original_process
        assert maker._enqueue is original_enqueue
        assert resting.DEFAULT_MAX_RESTING_FEED_AGE_MS == 500
        assert asyncio.iscoroutinefunction(maker._capture_pair)
    finally:
        maker._capture_pair = original_capture
