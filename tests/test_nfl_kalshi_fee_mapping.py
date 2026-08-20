from decimal import Decimal

import pytest

from src.gemini_kalshi_kalshi_fees import (
    KalshiSeriesFeeMetadata,
    kalshi_quadratic_taker_fee_centicent,
)

D = Decimal


def meta(series: str, fee_type: str = "quadratic_with_maker_fees", multiplier: str = "1"):
    return KalshiSeriesFeeMetadata(
        series_ticker=series,
        fee_type=fee_type,
        fee_multiplier=multiplier,
        last_updated_ts="test",
        scheduled_changes=(),
    )


def test_current_nfl_game_spread_total_maker_fee_label_maps_taker_only():
    for series in ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL"):
        row = meta(series)
        assert row.formula_status == "MAPPED_QUADRATIC_TAKER"
        assert row._nfl_maker_fee_taker_mapping_allowed is True
        fee = row.taker_fee_fn()(D("0.36"), D("1"))
        assert fee == D("0.0162")


def test_centicent_rounding_is_applied_to_position_cost_plus_taker_fee():
    # raw fee = 0.07 * .36 * .64 = .016128; total = .376128 -> .3762
    assert kalshi_quadratic_taker_fee_centicent(D("0.36"), D("1")) == D("0.0162")
    # .50 is exact: raw fee .0175 and total .5175 already lands on a centicent.
    assert kalshi_quadratic_taker_fee_centicent(D("0.50"), D("1")) == D("0.0175")


def test_nfl_mapping_requires_current_multiplier_one():
    row = meta("KXNFLGAME", multiplier="0.5")
    assert row.formula_status == "UNMAPPED_DO_NOT_TRADE"
    with pytest.raises(ValueError, match="unsupported Kalshi fee_type"):
        row.taker_fee_fn()


def test_non_nfl_maker_fee_series_remains_unmapped():
    row = meta("KXOTHERSPORT")
    assert row.formula_status == "UNMAPPED_DO_NOT_TRADE"
    assert row._nfl_maker_fee_taker_mapping_allowed is False
    with pytest.raises(ValueError, match="unsupported Kalshi fee_type"):
        row.taker_fee_fn()


def test_existing_quadratic_path_is_unchanged():
    row = meta("KXMLBHR", fee_type="quadratic", multiplier="0.5")
    assert row.formula_status == "MAPPED_QUADRATIC_TAKER"
    # Legacy path deliberately preserves conservative full-cent rounding.
    assert row.taker_fee_fn()(D("0.29"), D("1")) == D("0.01")
