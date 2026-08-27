from decimal import Decimal

from src.strict_mlb_prop_snapshot_v4 import _verified_series_fee


def _series(multiplier=1):
    return {"fee_type": "quadratic", "fee_multiplier": multiplier}


def _changes(multiplier=1, scheduled_ts="2026-08-27T20:00:00Z"):
    return {
        "series_fee_change_arr": [
            {
                "id": "effective",
                "series_ticker": "KXMLBHIT",
                "fee_type": "quadratic",
                "fee_multiplier": multiplier,
                "scheduled_ts": scheduled_ts,
            }
        ]
    }


def test_v4_returns_multiplier_only_when_quote_time_fee_evidence_verifies():
    multiplier, audit = _verified_series_fee(
        series="KXMLBHIT",
        series_meta=_series(1),
        fee_changes_body=_changes(1),
        observed_at="2026-08-27T21:00:00Z",
    )

    assert multiplier == Decimal("1")
    assert audit["verified"] is True
    assert audit["fee_source"] == "series_current_plus_fee_changes"
    assert audit["order_entry"] is False
    assert audit["live_probe_ready"] is False


def test_v4_fails_closed_when_effective_fee_change_disagrees_with_series_metadata():
    multiplier, audit = _verified_series_fee(
        series="KXMLBHIT",
        series_meta=_series(1),
        fee_changes_body=_changes(2),
        observed_at="2026-08-27T21:00:00Z",
    )

    assert multiplier is None
    assert audit["verified"] is False
    assert "CURRENT_SERIES_FEE_DISAGREES_WITH_EFFECTIVE_CHANGE" in audit["blockers"]


def test_v4_fails_closed_when_historical_fee_change_feed_is_unavailable():
    multiplier, audit = _verified_series_fee(
        series="KXMLBHIT",
        series_meta=_series(1),
        fee_changes_body=None,
        observed_at="2026-08-27T21:00:00Z",
    )

    assert multiplier is None
    assert audit["verified"] is False
    assert "FEE_CHANGE_FEED_MISSING_OR_MALFORMED" in audit["blockers"]
