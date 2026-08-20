from src.weather_fee_history_guard import resolve_fee_at_quote


def _series():
    return {
        "ticker": "KXHIGHNY",
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "last_updated_ts": "2026-08-20T15:00:00Z",
    }


def test_current_quote_can_use_current_series_fee():
    result = resolve_fee_at_quote(
        _series(), fee_changes=[], observed_at="2026-08-20T16:00:00Z"
    )
    assert result["verified"] is True
    assert result["fee_multiplier"] == 1
    assert result["source"] == "series_current"


def test_backdated_quote_cannot_inherit_current_series_fee_without_complete_history():
    result = resolve_fee_at_quote(
        _series(), fee_changes=[], observed_at="2026-08-20T14:00:00Z"
    )
    assert result["verified"] is False
    assert result["blockers"] == ["FEE_HISTORY_UNVERIFIED"]


def test_backdated_quote_uses_latest_effective_historical_change_when_history_complete():
    changes = [
        {
            "series_ticker": "KXHIGHNY",
            "fee_type": "quadratic",
            "fee_multiplier": 3,
            "scheduled_ts": "2026-08-19T10:00:00Z",
        },
        {
            "series_ticker": "KXHIGHNY",
            "fee_type": "quadratic",
            "fee_multiplier": 2,
            "scheduled_ts": "2026-08-20T13:00:00Z",
        },
        {
            "series_ticker": "KXHIGHNY",
            "fee_type": "quadratic",
            "fee_multiplier": 4,
            "scheduled_ts": "2026-08-20T14:30:00Z",
        },
    ]
    result = resolve_fee_at_quote(
        _series(),
        fee_changes=changes,
        observed_at="2026-08-20T14:00:00Z",
        historical_complete=True,
    )
    assert result["verified"] is True
    assert result["fee_multiplier"] == 2
    assert result["source"] == "historical_fee_change"


def test_backdated_quote_still_fails_when_complete_history_has_no_prior_effective_record():
    changes = [{
        "series_ticker": "KXHIGHNY",
        "fee_type": "quadratic",
        "fee_multiplier": 2,
        "scheduled_ts": "2026-08-20T14:30:00Z",
    }]
    result = resolve_fee_at_quote(
        _series(),
        fee_changes=changes,
        observed_at="2026-08-20T14:00:00Z",
        historical_complete=True,
    )
    assert result["verified"] is False
    assert result["blockers"] == ["FEE_HISTORY_UNVERIFIED"]


def test_change_after_series_snapshot_and_before_quote_overrides_current_metadata():
    changes = [{
        "series_ticker": "KXHIGHNY",
        "fee_type": "quadratic",
        "fee_multiplier": 2,
        "scheduled_ts": "2026-08-20T15:30:00Z",
    }]
    result = resolve_fee_at_quote(
        _series(), fee_changes=changes, observed_at="2026-08-20T16:00:00Z"
    )
    assert result["verified"] is True
    assert result["fee_multiplier"] == 2
    assert result["source"] == "scheduled_fee_change"


def test_truthy_string_does_not_certify_historical_completeness():
    result = resolve_fee_at_quote(
        _series(),
        fee_changes=[{
            "series_ticker": "KXHIGHNY",
            "fee_type": "quadratic",
            "fee_multiplier": 2,
            "scheduled_ts": "2026-08-20T13:00:00Z",
        }],
        observed_at="2026-08-20T14:00:00Z",
        historical_complete="true",  # type: ignore[arg-type]
    )
    assert result["verified"] is False
    assert result["blockers"] == ["FEE_HISTORY_UNVERIFIED"]
