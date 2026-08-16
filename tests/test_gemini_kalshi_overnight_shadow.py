from decimal import Decimal

import pytest

from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata, SERIES
from src.gemini_kalshi_overnight_shadow import (
    seconds_to_next_boundary,
    validated_common_multiplier,
)


def _row(ticker: str, *, fee_type: str = "quadratic", multiplier: str = "1"):
    return KalshiSeriesFeeMetadata(
        series_ticker=ticker,
        fee_type=fee_type,
        fee_multiplier=multiplier,
        last_updated_ts="2026-08-15T00:00:00Z",
        scheduled_changes=(),
    )


def test_seconds_to_next_boundary():
    assert seconds_to_next_boundary(0) == 900
    assert seconds_to_next_boundary(1) == 899
    assert seconds_to_next_boundary(899.5) == pytest.approx(0.5)
    assert seconds_to_next_boundary(900) == 900
    assert seconds_to_next_boundary(901) == 899


def test_validated_common_multiplier_accepts_verified_quadratic_rows():
    rows = [_row(ticker) for ticker in SERIES]
    assert validated_common_multiplier(rows) == Decimal("1")


def test_validated_common_multiplier_fails_closed_on_unknown_fee_type():
    rows = [_row(ticker) for ticker in SERIES]
    rows[0] = _row(SERIES[0], fee_type="future_new_type")
    with pytest.raises(ValueError, match="unmapped Kalshi fee type"):
        validated_common_multiplier(rows)


def test_validated_common_multiplier_fails_closed_on_mixed_multiplier():
    rows = [_row(ticker) for ticker in SERIES]
    rows[-1] = _row(SERIES[-1], multiplier="2")
    with pytest.raises(ValueError, match="mixed Kalshi fee multipliers"):
        validated_common_multiplier(rows)


def test_validated_common_multiplier_fails_closed_on_missing_series():
    rows = [_row(ticker) for ticker in SERIES[:-1]]
    with pytest.raises(ValueError, match="expected"):
        validated_common_multiplier(rows)
