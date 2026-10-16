"""Tests for the 15m crypto favorite/longshot study with synthetic data."""

import pytest

from src.crypto_15m_longshot_study import (
    PRICE_BUCKETS,
    study,
    wilson_95,
    yes_mid_cents,
)

CLOSE_TS = 1_760_000_000


def _candle(end_ts: int, bid: float, ask: float) -> dict:
    return {"end_period_ts": end_ts, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}}


class FakeApi:
    """Stands in for Kalshi: serves canned settled markets and candlesticks."""

    def __init__(self, markets: list[dict], candles_by_ticker: dict[str, list[dict]]):
        self._markets = markets
        self._candles = candles_by_ticker

    def get_json(self, path, params=None):
        assert path == "/markets"
        return {"markets": self._markets, "cursor": ""}

    def get(self, path, params=None):  # pragma: no cover - study() uses candles() shim
        raise AssertionError("unexpected raw get")


@pytest.fixture(autouse=True)
def _stub_candles(monkeypatch):
    def fake_candles(api, series, ticker, start_ts, end_ts):
        return api._candles.get(ticker, [])

    monkeypatch.setattr("src.crypto_15m_longshot_study.candles", fake_candles)


class TestYesMid:
    def test_mid_is_touch_midpoint(self):
        assert yes_mid_cents(_candle(0, 90, 92)) == 91.0

    @pytest.mark.parametrize("bid,ask", [(None, 92), (0, 92), (92, 90), (98, 100)])
    def test_rejects_unusable_touch(self, bid, ask):
        """Missing, inverted, or out-of-range touches must not become data points."""
        assert yes_mid_cents(_candle(0, bid, ask)) is None


class TestWilson:
    def test_empty_sample_has_no_interval(self):
        assert wilson_95(0, 0) is None

    def test_interval_brackets_point_estimate(self):
        low, high = wilson_95(90, 100)
        assert low < 0.90 < high

    def test_interval_tightens_with_sample_size(self):
        small_low, small_high = wilson_95(9, 10)
        big_low, big_high = wilson_95(900, 1000)
        assert (big_high - big_low) < (small_high - small_low)


class TestStudy:
    def test_favorite_win_rate_and_longshot_edge(self):
        """Favorite at 91c wins 3 of 4 windows → longshot realized 25% vs 9% implied."""
        markets, candles = [], {}
        for i, result in enumerate(["yes", "yes", "yes", "no"]):
            ticker = f"KXBTC15M-{i}"
            markets.append({"ticker": ticker, "result": result, "close_ts": CLOSE_TS})
            # YES favored at 91c with 5 minutes left in every window.
            candles[ticker] = [_candle(CLOSE_TS - 5 * 60, 90, 92)]

        out = study(FakeApi(markets, candles), "btc", "KXBTC15M", 30, 100)

        assert out["windows_usable"] == 4
        (row,) = out["favorite_by_minute"]
        assert row["minutes_remaining"] == 5
        assert row["n"] == 4
        assert row["favorite_win_rate"] == 0.75

        (cal,) = out["longshot_calibration"]
        assert cal["longshot_price_bucket"] == "6-10c"
        assert cal["implied_win_rate"] == 0.09
        assert cal["realized_win_rate"] == 0.25
        # Longshot won MORE than its price implied → selling it had negative edge.
        assert cal["edge_pct_points"] == pytest.approx(-16.0)

    def test_coin_flip_at_50c_is_excluded(self):
        """A 50c mid has no favorite, so it must not be scored either way."""
        markets = [{"ticker": "T", "result": "yes", "close_ts": CLOSE_TS}]
        candles = {"T": [_candle(CLOSE_TS - 3 * 60, 49, 51)]}

        out = study(FakeApi(markets, candles), "btc", "KXBTC15M", 30, 100)

        assert out["windows_usable"] == 0
        assert out["favorite_by_minute"] == []

    def test_unresolved_and_candleless_windows_are_skipped_not_counted(self):
        markets = [
            {"ticker": "A", "result": "", "close_ts": CLOSE_TS},
            {"ticker": "B", "result": "yes", "close_ts": None},
            {"ticker": "C", "result": "yes", "close_ts": CLOSE_TS},
        ]
        out = study(FakeApi(markets, {}), "btc", "KXBTC15M", 30, 100)

        assert out["windows_usable"] == 0
        assert out["skipped"] == {
            "unresolved_result": 1,
            "missing_close_ts": 1,
            "no_candles": 1,
        }

    def test_each_minute_bucket_scored_independently(self):
        """One window contributes one point per minute-remaining bucket."""
        markets = [{"ticker": "T", "result": "no", "close_ts": CLOSE_TS}]
        candles = {"T": [_candle(CLOSE_TS - m * 60, 70, 72) for m in (3, 4, 5, 6)]}

        out = study(FakeApi(markets, candles), "btc", "KXBTC15M", 30, 100)

        assert [r["minutes_remaining"] for r in out["favorite_by_minute"]] == [3, 4, 5, 6]
        # YES was favored at 71c and lost every bucket.
        assert all(r["favorite_win_rate"] == 0.0 for r in out["favorite_by_minute"])
        assert all(c["longshot_price_bucket"] == "21-35c" for c in out["longshot_calibration"])
        assert all(c["realized_win_rate"] == 1.0 for c in out["longshot_calibration"])

    def test_output_is_never_labeled_realized(self):
        out = study(FakeApi([], {}), "btc", "KXBTC15M", 30, 100)
        assert out["classification"] == "HISTORICAL_REPLAY_NOT_REALIZED"
        assert out["fees_applied"] is False


def test_price_buckets_are_longshot_side_only():
    """Buckets must stay <=50c; a >50c 'longshot' would be a mislabeled favorite."""
    assert all(low < high <= 50 for low, high in PRICE_BUCKETS)
