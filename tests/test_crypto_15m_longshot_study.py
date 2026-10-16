"""Tests for the 15m crypto buy-vs-sell calibration study, on synthetic data."""

import math

import pytest

from src.crypto_15m_longshot_study import (
    NINE_SERIES,
    PRICE_BANDS,
    Cells,
    band_of,
    collect,
    headline,
    taker_fee_cents_per_contract,
    verdict,
    wilson_95,
    yes_mid_cents,
)

CLOSE_TS = 1_760_000_000


def _candle(end_ts: int, bid: float, ask: float) -> dict:
    return {"end_period_ts": end_ts, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}}


class FakeApi:
    def __init__(self, candles_by_ticker: dict[str, list[dict]]):
        self._candles = candles_by_ticker


@pytest.fixture(autouse=True)
def _stub_candles(monkeypatch):
    def fake_candles(api, series, ticker, start_ts, end_ts):
        return api._candles.get(ticker, [])

    monkeypatch.setattr("src.crypto_15m_longshot_study.candles", fake_candles)


def _window(ticker: str, result: str, minutes: list[int], bid: float, ask: float):
    market = {"ticker": ticker, "result": result, "close_ts": CLOSE_TS}
    return market, [_candle(CLOSE_TS - m * 60, bid, ask) for m in minutes]


def _run(markets_and_candles, series="KXBTC15M"):
    markets, candles = [], {}
    for market, rows in markets_and_candles:
        markets.append(market)
        candles[market["ticker"]] = rows
    cells, pooled = Cells(), Cells()
    stats = collect(FakeApi(candles), series, markets, cells, pooled)
    return cells, pooled, stats


class TestSeriesUniverse:
    def test_covers_the_nine_fader_series(self):
        assert len(NINE_SERIES) == 9
        assert set(NINE_SERIES.values()) == {
            "KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
            "KXBNB15M", "KXHYPE15M", "KXNEAR15M", "KXZEC15M",
        }


class TestFees:
    def test_single_contract_pays_the_whole_ceil(self):
        """At 8c a lone contract pays a full cent -- 12.5% of the stake."""
        assert taker_fee_cents_per_contract(8, 1) == pytest.approx(1.0)

    def test_per_contract_fee_falls_with_order_size(self):
        """Same price, bigger order: the ceil is amortized."""
        assert taker_fee_cents_per_contract(8, 100) == pytest.approx(0.52)
        assert taker_fee_cents_per_contract(8, 100) < taker_fee_cents_per_contract(8, 1)

    def test_fee_is_convex_peaking_at_50c(self):
        mid = taker_fee_cents_per_contract(50, 100)
        assert mid > taker_fee_cents_per_contract(10, 100)
        assert mid > taker_fee_cents_per_contract(90, 100)

    def test_fee_is_symmetric_across_the_two_sides(self):
        """Both legs of one window cost the same, so the no-trade band is centered."""
        assert (taker_fee_cents_per_contract(8, 100)
                == pytest.approx(taker_fee_cents_per_contract(92, 100)))

    def test_rejects_nonpositive_count(self):
        with pytest.raises(ValueError):
            taker_fee_cents_per_contract(8, 0)


class TestWilson:
    def test_empty_sample_has_no_interval(self):
        assert wilson_95(0, 0) is None

    def test_interval_brackets_point_estimate(self):
        low, high = wilson_95(90, 100)
        assert low < 0.90 < high

    def test_interval_tightens_with_sample_size(self):
        small = wilson_95(9, 10)
        big = wilson_95(900, 1000)
        assert (big[1] - big[0]) < (small[1] - small[0])


class TestVerdict:
    FEE = 0.005  # half a cent, as a fraction

    def test_underpowered_below_min_n(self):
        assert verdict((0.20, 0.30), 0.08, self.FEE, min_n=200, n=10) == "UNDERPOWERED"

    def test_buy_requires_interval_above_price_plus_fee(self):
        assert verdict((0.12, 0.20), 0.08, self.FEE, 200, 500) == "BUY_LONGSHOT"

    def test_sell_requires_interval_below_price_minus_fee(self):
        assert verdict((0.02, 0.05), 0.08, self.FEE, 200, 500) == "SELL_LONGSHOT"

    def test_interval_straddling_the_price_earns_nothing(self):
        assert verdict((0.06, 0.11), 0.08, self.FEE, 200, 500) == "NO_EDGE"

    def test_point_estimate_inside_the_fee_band_earns_nothing(self):
        """Realized 8.3% vs implied 8.0% is edge on paper and fees eat it."""
        assert verdict((0.081, 0.084), 0.08, self.FEE, 200, 500) == "NO_EDGE"

    @pytest.mark.parametrize("ci", [(0.0801, 0.0849), (0.0751, 0.0799)])
    def test_a_wide_fee_band_blocks_both_directions(self, ci):
        assert verdict(ci, 0.08, 0.005, 200, 500) == "NO_EDGE"


class TestBands:
    def test_bands_are_longshot_side_only(self):
        """A band above 50c would be a mislabeled favorite."""
        assert all(low < high <= 50 for low, high in PRICE_BANDS)

    def test_bands_do_not_overlap(self):
        seen = set()
        for low, high in PRICE_BANDS:
            cents = set(range(low, high + 1))
            assert not (cents & seen)
            seen |= cents

    @pytest.mark.parametrize("price,expected", [
        (8, "6-10c"), (1, "1-5c"), (49, "36-49c"), (50, None), (0.5, None), (75, None),
    ])
    def test_band_assignment(self, price, expected):
        assert band_of(price) == expected


class TestYesMid:
    def test_mid_is_touch_midpoint(self):
        assert yes_mid_cents(_candle(0, 90, 92)) == 91.0

    @pytest.mark.parametrize("bid,ask", [(None, 92), (0, 92), (92, 90), (98, 100)])
    def test_rejects_unusable_touch(self, bid, ask):
        assert yes_mid_cents(_candle(0, bid, ask)) is None


class TestCollect:
    def test_overpriced_longshot_earns_sell(self):
        """Longshot at 8c wins 2 of 400 (0.5%) -> selling it clears fees."""
        data = []
        for i in range(400):
            data.append(_window(f"T{i}", "yes" if i >= 2 else "no", [5], 90, 94))
        cells, _, stats = _run(data)

        assert stats["windows_usable"] == 400
        (row,) = cells.rows(count=100, min_n=200)
        assert row["longshot_price_band"] == "6-10c"
        assert row["avg_longshot_price_cents"] == pytest.approx(8.0)
        assert row["realized_win_rate"] == 0.005
        assert row["verdict"] == "SELL_LONGSHOT"
        assert row["ev_sell_longshot_cents"] > 0
        assert row["ev_buy_longshot_cents"] < 0

    def test_underpriced_longshot_earns_buy(self):
        """Longshot at 8c wins 25% -> this is the SF-002 inversion, if it is real."""
        data = []
        for i in range(400):
            data.append(_window(f"T{i}", "no" if i % 4 == 0 else "yes", [5], 90, 94))
        cells, _, _ = _run(data)

        (row,) = cells.rows(count=100, min_n=200)
        assert row["realized_win_rate"] == 0.25
        assert row["verdict"] == "BUY_LONGSHOT"
        assert row["ev_buy_longshot_cents"] == pytest.approx(25.0 - 8.0 - 0.52, abs=1e-6)

    def test_fairly_priced_longshot_earns_nothing(self):
        """Realized 8% at an 8c price is exactly the no-edge case."""
        data = []
        for i in range(400):
            data.append(_window(f"T{i}", "no" if i % 25 < 2 else "yes", [5], 90, 94))
        cells, _, _ = _run(data)

        (row,) = cells.rows(count=100, min_n=200)
        assert row["realized_win_rate"] == 0.08
        assert row["verdict"] == "NO_EDGE"

    def test_favorite_win_rate_is_the_complement(self):
        data = [_window("A", "yes", [5], 90, 94), _window("B", "no", [5], 90, 94)]
        cells, _, _ = _run(data)

        (fav,) = cells.favorite_rows()
        assert fav["minutes_remaining"] == 5
        assert fav["n"] == 2
        assert fav["favorite_win_rate"] == 0.5

    def test_each_minute_scored_independently(self):
        data = [_window("T", "no", [3, 4, 5, 6], 70, 72)]
        cells, _, _ = _run(data)

        rows = cells.rows(count=100, min_n=1)
        assert [r["minutes_remaining"] for r in rows] == [3, 4, 5, 6]
        assert all(r["longshot_price_band"] == "21-35c" for r in rows)
        assert all(r["realized_win_rate"] == 1.0 for r in rows)

    def test_coin_flip_at_50c_is_excluded(self):
        cells, _, stats = _run([_window("T", "yes", [3], 49, 51)])
        assert stats["windows_usable"] == 0
        assert cells.rows(count=100, min_n=1) == []

    def test_window_outside_every_band_is_not_counted_usable(self):
        """A 45c/55c market has no longshot in range; it must not inflate coverage."""
        cells, _, stats = _run([_window("T", "yes", [3], 99, 99.5)])
        assert stats["windows_usable"] == 0
        assert cells.favorite_rows() == []

    def test_unresolved_and_candleless_windows_are_skipped(self):
        markets = [
            {"ticker": "A", "result": "", "close_ts": CLOSE_TS},
            {"ticker": "B", "result": "yes", "close_ts": None},
            {"ticker": "C", "result": "yes", "close_ts": CLOSE_TS},
        ]
        cells, pooled = Cells(), Cells()
        stats = collect(FakeApi({}), "KXBTC15M", markets, cells, pooled)

        assert stats["windows_usable"] == 0
        assert stats["skipped"] == {
            "unresolved_result": 1, "missing_close_ts": 1, "no_candles": 1,
        }

    def test_pooled_table_accumulates_across_series(self):
        pooled = Cells()
        for series in ("KXBTC15M", "KXETH15M"):
            market, rows = _window(f"{series}-1", "yes", [5], 90, 94)
            collect(FakeApi({market["ticker"]: rows}), series, [market], Cells(), pooled)

        (row,) = pooled.rows(count=100, min_n=1)
        assert row["n"] == 2


class TestHeadline:
    def test_isolates_the_live_fader_entry_cells(self):
        """The live gate is 90-94c at 250-350s, i.e. 6-10c longshot at 4-6 min."""
        rows = [
            {"verdict": "NO_EDGE", "longshot_price_band": "6-10c",
             "minutes_remaining": 5, "ev_buy_longshot_cents": 1.0,
             "ev_sell_longshot_cents": 1.0},
            {"verdict": "NO_EDGE", "longshot_price_band": "6-10c",
             "minutes_remaining": 9, "ev_buy_longshot_cents": 1.0,
             "ev_sell_longshot_cents": 1.0},
            {"verdict": "NO_EDGE", "longshot_price_band": "21-35c",
             "minutes_remaining": 5, "ev_buy_longshot_cents": 1.0,
             "ev_sell_longshot_cents": 1.0},
        ]
        out = headline(rows)
        assert len(out["fader_entry_cells_6_10c_at_4_6min"]) == 1
        assert out["fader_entry_cells_6_10c_at_4_6min"][0]["minutes_remaining"] == 5

    def test_counts_and_best_cells(self):
        rows = [
            {"verdict": "BUY_LONGSHOT", "longshot_price_band": "6-10c",
             "minutes_remaining": 5, "ev_buy_longshot_cents": 3.0,
             "ev_sell_longshot_cents": -3.0},
            {"verdict": "BUY_LONGSHOT", "longshot_price_band": "1-5c",
             "minutes_remaining": 5, "ev_buy_longshot_cents": 9.0,
             "ev_sell_longshot_cents": -9.0},
            {"verdict": "UNDERPOWERED", "longshot_price_band": "1-5c",
             "minutes_remaining": 1, "ev_buy_longshot_cents": 0.0,
             "ev_sell_longshot_cents": 0.0},
        ]
        out = headline(rows)
        assert out["cells_buy_longshot"] == 2
        assert out["cells_underpowered"] == 1
        assert out["best_buy_cell"]["ev_buy_longshot_cents"] == 9.0
        assert out["best_sell_cell"] is None

    def test_empty_input_is_safe(self):
        out = headline([])
        assert out["cells_total"] == 0
        assert out["best_buy_cell"] is None
        assert out["fader_entry_cells_6_10c_at_4_6min"] == []
