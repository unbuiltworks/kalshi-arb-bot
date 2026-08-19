from decimal import Decimal

import pytest

from src.gemini_kalshi_kalshi_fees import (
    kalshi_quadratic_maker_fee,
    kalshi_quadratic_taker_fee,
    normalize_series_fee,
)

D = Decimal


def test_normalize_series_fee_maps_current_quadratic_and_keeps_scheduled_metadata():
    row = normalize_series_fee(
        "KXBTC15M",
        {"series": {"ticker": "KXBTC15M", "fee_type": "quadratic", "fee_multiplier": 1.5, "last_updated_ts": "2026-08-15T00:00:00Z"}},
        {"series_fee_change_arr": [
            {"series_ticker": "KXBTC15M", "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 2, "scheduled_ts": "2026-08-16T00:00:00Z"},
            {"series_ticker": "OTHER", "fee_type": "flat", "fee_multiplier": 9},
        ]},
    )
    assert row.series_ticker == "KXBTC15M"
    assert row.fee_type == "quadratic"
    assert row.fee_multiplier == "1.5"
    assert len(row.scheduled_changes) == 1
    assert row.formula_status == "MAPPED_QUADRATIC_TAKER"


def test_quadratic_taker_fee_uses_current_centicent_rounding():
    assert kalshi_quadratic_taker_fee(D("0.44"), D("1")) == D("0.0173")
    assert kalshi_quadratic_taker_fee(D("0.44"), D("10")) == D("0.1725")
    assert kalshi_quadratic_taker_fee(
        D("0.50"), D("100"), fee_multiplier=D("1.5")
    ) == D("2.6250")


def test_quadratic_maker_fee_uses_published_curve_and_explicit_multiplier():
    assert kalshi_quadratic_maker_fee(D("0.44"), D("1")) == D("0.0000")
    assert kalshi_quadratic_maker_fee(
        D("0.44"), D("1"), maker_fee_multiplier=D("1")
    ) == D("0.0044")


def test_metadata_taker_fee_fn_binds_live_multiplier():
    row = normalize_series_fee(
        "KXETH15M",
        {"series": {"ticker": "KXETH15M", "fee_type": "quadratic", "fee_multiplier": 1}},
        {"series_fee_change_arr": []},
    )
    assert row.taker_fee_fn()(D("0.50"), D("10")) == D("0.1750")


def test_unknown_fee_type_stays_fail_closed():
    row = normalize_series_fee(
        "KXSOL15M",
        {"series": {"ticker": "KXSOL15M", "fee_type": "flat", "fee_multiplier": 1}},
        {"series_fee_change_arr": []},
    )
    assert row.formula_status == "UNMAPPED_DO_NOT_TRADE"
    with pytest.raises(ValueError, match="unsupported Kalshi fee_type"):
        row.taker_fee_fn()


def test_normalize_series_fee_rejects_missing_metadata():
    with pytest.raises(ValueError, match="missing Kalshi fee metadata"):
        normalize_series_fee("KXETH15M", {"series": {"ticker": "KXETH15M"}}, {})


def test_normalize_series_fee_rejects_wrong_series():
    with pytest.raises(ValueError, match="series ticker mismatch"):
        normalize_series_fee("KXSOL15M", {"series": {"ticker": "KXXRP15M", "fee_type": "quadratic", "fee_multiplier": 1}}, {})
