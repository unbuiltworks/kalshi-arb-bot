"""Regression: ROUTE B replay must use actual UTC market boundaries."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "analysis/l5/issue208/route_b_replay.py"
sys.path.insert(0, str(PATH.parent))
SPEC = importlib.util.spec_from_file_location("route_b_replay_cutoff", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("market,expected", [
    ("cpc-btc-updown-15m-2026-11-14-1345z", (2026, 11, 14, 13, 45)),
    ("cpc-btc-updown-15m-2026-11-14-0000z", (2026, 11, 14, 0, 0)),
    ("cpc-btc-updown-5m-2026-11-14-1355z", (2026, 11, 14, 13, 55)),
])
def test_window_start_is_market_utc_not_quote_minus_120(market, expected):
    assert MODULE.market_window_start(market) == datetime(
        *expected, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("market", [
    "cpc-btc-updown-15m-2026-11-14-1346z",
    "cpc-btc-updown-15m-2026-11-14-1360z",
    "cpc-btc-updown-15m-2026-02-30-1345z",
    "cpc-btc-updown-15m-2026-11-14-1345z|SHADOW",
    "unknown-market",
])
def test_unknown_or_misaligned_market_fails_closed(market):
    with pytest.raises(ValueError):
        MODULE.market_window_start(market)


@pytest.mark.parametrize("duration,seconds", [("15m", 900), ("5m", 300)])
def test_sequential_book_must_follow_confirmed_opening_and_precede_expiry(duration, seconds):
    market = f"cpc-btc-updown-{duration}-2026-11-14-1345z" if duration == "15m" else "cpc-btc-updown-5m-2026-11-14-1355z"
    start = MODULE.market_window_start(market)
    gate = MODULE.causally_eligible_book
    assert not gate(market, None, start + 20)
    assert not gate(market, start + 30, None)
    assert not gate(market, start + 30, start + 29)
    assert gate(market, start + 30, start + 30)
    assert gate(market, start + 30, start + seconds - 1)
    assert not gate(market, start + 30, start + seconds)
