from src.mlb_kalshi_fee_guard import audit_current_series_fee


def _series(multiplier=1, fee_type="quadratic"):
    return {"fee_type": fee_type, "fee_multiplier": multiplier}


def _changes(*rows):
    return {"series_fee_change_arr": list(rows)}


def test_current_series_fee_accepts_matching_latest_effective_change_and_future_schedule():
    result = audit_current_series_fee(
        series_ticker="KXMLBHIT",
        series_meta=_series(1),
        observed_at="2026-08-27T21:00:00Z",
        fee_changes_body=_changes(
            {
                "id": "old",
                "series_ticker": "KXMLBHIT",
                "fee_type": "quadratic",
                "fee_multiplier": 1,
                "scheduled_ts": "2026-08-01T00:00:00Z",
            },
            {
                "id": "future",
                "series_ticker": "KXMLBHIT",
                "fee_type": "quadratic",
                "fee_multiplier": 2,
                "scheduled_ts": "2026-08-28T00:00:00Z",
            },
        ),
    )
    assert result["verified"] is True
    assert result["fee_multiplier"] == "1"
    assert result["effective_change_id"] == "old"
    assert result["next_change_scheduled_ts"] == "2026-08-28T00:00:00Z"
    assert result["order_entry"] is False
    assert result["live_probe_ready"] is False


def test_current_series_fee_fails_closed_when_effective_change_disagrees():
    result = audit_current_series_fee(
        series_ticker="KXMLBHIT",
        series_meta=_series(1),
        observed_at="2026-08-27T21:00:00Z",
        fee_changes_body=_changes(
            {
                "id": "effective",
                "series_ticker": "KXMLBHIT",
                "fee_type": "quadratic",
                "fee_multiplier": 2,
                "scheduled_ts": "2026-08-27T20:00:00Z",
            }
        ),
    )
    assert result["verified"] is False
    assert "CURRENT_SERIES_FEE_DISAGREES_WITH_EFFECTIVE_CHANGE" in result["blockers"]


def test_current_series_fee_requires_complete_change_feed_shape():
    result = audit_current_series_fee(
        series_ticker="KXMLBHIT",
        series_meta=_series(1),
        observed_at="2026-08-27T21:00:00Z",
        fee_changes_body={},
    )
    assert result["verified"] is False
    assert "FEE_CHANGE_FEED_MISSING_OR_MALFORMED" in result["blockers"]


def test_current_series_fee_rejects_wrong_series_records_and_naive_quote_time():
    result = audit_current_series_fee(
        series_ticker="KXMLBHIT",
        series_meta=_series(1),
        observed_at="2026-08-27T21:00:00",
        fee_changes_body=_changes(
            {
                "id": "wrong",
                "series_ticker": "KXMLBTB",
                "fee_type": "quadratic",
                "fee_multiplier": 1,
                "scheduled_ts": "2026-08-01T00:00:00Z",
            }
        ),
    )
    assert result["verified"] is False
    assert "FEE_QUOTE_TIME_INVALID_OR_NAIVE" in result["blockers"]
    assert "FEE_CHANGE_SERIES_MISMATCH" in result["blockers"]


def test_current_series_fee_rejects_nonfinite_or_unsupported_values():
    result = audit_current_series_fee(
        series_ticker="KXMLBHIT",
        series_meta=_series("NaN", fee_type="flat"),
        observed_at="2026-08-27T21:00:00Z",
        fee_changes_body=_changes(),
    )
    assert result["verified"] is False
    assert "FEE_TYPE_UNSUPPORTED_OR_MISSING" in result["blockers"]
    assert "FEE_MULTIPLIER_INVALID_OR_MISSING" in result["blockers"]
