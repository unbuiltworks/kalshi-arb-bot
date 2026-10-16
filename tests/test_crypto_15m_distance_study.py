"""Tests for the distance-conditional study, on synthetic data."""

import math

import pytest

from src.crypto_15m_distance_study import (
    DISTANCE_BUCKETS,
    SPOT_PRODUCT,
    bucket_of,
    distances,
    one_minute_vol,
    spread_report,
)
from src.crypto_15m_longshot_study import NINE_SERIES


def _flat_spot(n=40, start=1_792_000_000, px=100.0, step=0.0):
    return {start + 60 * i: px + step * i for i in range(n)}


class TestCoverage:
    def test_every_series_has_a_spot_product(self):
        assert set(SPOT_PRODUCT) == set(NINE_SERIES.values())

    def test_spot_feed_is_not_kalshi(self):
        """A Kalshi-derived distance is a transform of the price; it cannot test lag."""
        from src.crypto_15m_distance_study import COINBASE_BASE
        assert "kalshi" not in COINBASE_BASE.lower()


class TestVol:
    def test_flat_series_has_no_vol(self):
        assert one_minute_vol(_flat_spot(), 1_792_000_000 + 60 * 39) is None

    def test_too_few_bars_returns_none(self):
        assert one_minute_vol(_flat_spot(n=3), 1_792_000_000 + 120) is None

    def test_moving_series_has_positive_vol(self):
        spot = {1_792_000_000 + 60 * i: 100.0 + (i % 3) for i in range(40)}
        assert one_minute_vol(spot, 1_792_000_000 + 60 * 39) > 0

    def test_only_bars_at_or_before_the_instant_are_used(self):
        """Using later bars would leak the future into the decision."""
        spot = {1_792_000_000 + 60 * i: 100.0 for i in range(20)}
        spot.update({1_792_000_000 + 60 * i: 100.0 + 50 * i for i in range(20, 40)})
        # Flat up to bar 19 -> no vol, despite wild moves afterwards.
        assert one_minute_vol(spot, 1_792_000_000 + 60 * 19) is None


class TestDistances:
    def test_horizon_scaling_divides_by_sqrt_minutes(self):
        """SF-002: the gate's unit is horizon-blind; the z-score is not."""
        raw, z = distances(spot_px=110.0, strike=100.0, vol=0.01, minutes_remaining=5)
        assert raw == pytest.approx(10.0)
        assert z == pytest.approx(10.0 / math.sqrt(5))

    def test_the_gate_overstates_by_2_24x_at_five_minutes(self):
        raw, z = distances(110.0, 100.0, 0.01, 5)
        assert raw / z == pytest.approx(2.2360, abs=1e-3)

    def test_raw_unit_is_identical_across_horizons(self):
        """The bug, pinned: same number at 1 minute and at 14."""
        assert distances(110.0, 100.0, 0.01, 1)[0] == distances(110.0, 100.0, 0.01, 14)[0]

    def test_horizon_z_shrinks_as_the_window_opens(self):
        assert distances(110.0, 100.0, 0.01, 1)[1] > distances(110.0, 100.0, 0.01, 14)[1]

    def test_sign_is_preserved(self):
        assert distances(90.0, 100.0, 0.01, 5)[0] < 0

    @pytest.mark.parametrize("kwargs", [
        {"strike": 0.0}, {"vol": 0.0}, {"vol": None}, {"minutes_remaining": 0},
    ])
    def test_unusable_inputs_return_none(self, kwargs):
        base = {"spot_px": 110.0, "strike": 100.0, "vol": 0.01, "minutes_remaining": 5}
        assert distances(**{**base, **kwargs}) is None


class TestBuckets:
    def test_buckets_are_contiguous_from_zero(self):
        assert DISTANCE_BUCKETS[0][0] == 0.0
        for (_, prev_hi), (next_lo, _) in zip(DISTANCE_BUCKETS, DISTANCE_BUCKETS[1:]):
            assert prev_hi == next_lo

    def test_bucketing_is_on_magnitude(self):
        """Direction is in the favorite's identity already; only size matters here."""
        assert bucket_of(1.2) == bucket_of(-1.2)

    @pytest.mark.parametrize("value,expected", [
        (0.0, "0.0-0.5"), (0.75, "0.5-1.0"), (1.75, "1.75-2.5"), (3.0, "2.5-99.0"),
    ])
    def test_assignment(self, value, expected):
        assert bucket_of(value) == expected

    def test_absurd_value_falls_outside(self):
        assert bucket_of(1e6) is None


def _cell(band, bucket, n, rate, ci):
    return {"longshot_price_band": band, "distance_bucket": bucket, "n": n,
            "realized_favorite_win_rate": rate, "ci95_realized": ci}


class TestSpreadReport:
    def test_disjoint_intervals_mean_distance_adds_information(self):
        rows = [
            _cell("6-10c", "0.0-0.5", 300, 0.80, [0.75, 0.85]),
            _cell("6-10c", "2.5-99.0", 300, 0.97, [0.94, 0.99]),
        ]
        (out,) = spread_report(rows, min_n=100)
        assert out["reading"] == "DISTANCE_ADDS_INFORMATION"
        assert out["intervals_disjoint"] is True
        assert out["realized_spread_pct_points"] == pytest.approx(17.0)
        assert out["lowest_bucket"] == "0.0-0.5"
        assert out["highest_bucket"] == "2.5-99.0"

    def test_overlapping_intervals_mean_the_price_already_knows(self):
        rows = [
            _cell("6-10c", "0.0-0.5", 300, 0.91, [0.88, 0.94]),
            _cell("6-10c", "2.5-99.0", 300, 0.93, [0.90, 0.96]),
        ]
        (out,) = spread_report(rows, min_n=100)
        assert out["reading"] == "PRICE_ALREADY_CONTAINS_DISTANCE"
        assert out["intervals_disjoint"] is False

    def test_underpowered_buckets_cannot_manufacture_a_spread(self):
        rows = [
            _cell("6-10c", "0.0-0.5", 300, 0.91, [0.88, 0.94]),
            _cell("6-10c", "2.5-99.0", 2, 0.00, [0.0, 0.80]),
        ]
        assert spread_report(rows, min_n=100) == []

    def test_a_single_powered_bucket_yields_no_comparison(self):
        rows = [_cell("6-10c", "0.0-0.5", 300, 0.91, [0.88, 0.94])]
        assert spread_report(rows, min_n=100) == []

    def test_bands_are_reported_separately(self):
        """The test is conditional on price; bands must never be pooled."""
        rows = [
            _cell("6-10c", "0.0-0.5", 300, 0.91, [0.88, 0.94]),
            _cell("6-10c", "2.5-99.0", 300, 0.93, [0.90, 0.96]),
            _cell("21-35c", "0.0-0.5", 300, 0.70, [0.65, 0.75]),
            _cell("21-35c", "2.5-99.0", 300, 0.74, [0.69, 0.79]),
        ]
        out = spread_report(rows, min_n=100)
        assert [r["longshot_price_band"] for r in out] == ["21-35c", "6-10c"]
