import pytest

from src.gemini_kalshi_kalshi_fees import normalize_series_fee


def test_normalize_series_fee_keeps_current_and_scheduled_metadata_fail_closed():
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
    assert row.formula_status == "UNMAPPED_DO_NOT_TRADE"


def test_normalize_series_fee_rejects_missing_metadata():
    with pytest.raises(ValueError, match="missing Kalshi fee metadata"):
        normalize_series_fee("KXETH15M", {"series": {"ticker": "KXETH15M"}}, {})


def test_normalize_series_fee_rejects_wrong_series():
    with pytest.raises(ValueError, match="series ticker mismatch"):
        normalize_series_fee("KXSOL15M", {"series": {"ticker": "KXXRP15M", "fee_type": "quadratic", "fee_multiplier": 1}}, {})
