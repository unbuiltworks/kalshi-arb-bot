from src.sports_kp_mlb_start_skew_diagnostic import (
    _percentile,
    _raw_kalshi_start_fields,
    _raw_poly_start_fields,
)


def test_percentile_is_nearest_rank_and_bounded():
    values = [100, 200, 300, 400]
    assert _percentile(values, 0.50) == 200
    assert _percentile(values, 0.90) == 400
    assert _percentile([], 0.50) is None


def test_raw_poly_start_fields_are_diagnostic_only_values():
    event = {"startTime": "2026-08-26T23:05:00Z", "startDate": "2026-08-26"}
    market = {"gameStartTime": "2026-08-26T23:05:00Z", "startDate": "2026-08-26T22:00:00Z"}
    assert _raw_poly_start_fields(event, market) == {
        "market_gameStartTime": "2026-08-26T23:05:00Z",
        "event_startTime": "2026-08-26T23:05:00Z",
        "event_startDate": "2026-08-26",
        "market_startDate": "2026-08-26T22:00:00Z",
    }


def test_raw_kalshi_start_fields_include_ticker_and_expiry_candidates():
    event = {"event_ticker": "KXMLBGAME-26AUG26-NYYBOS", "strike_date": "2026-08-27T00:00:00Z"}
    market = {"expected_expiration_time": "2026-08-27T03:00:00Z", "close_time": "2026-08-27T03:00:00Z"}
    assert _raw_kalshi_start_fields(event, market) == {
        "event_ticker": "KXMLBGAME-26AUG26-NYYBOS",
        "event_strike_date": "2026-08-27T00:00:00Z",
        "market_expected_expiration_time": "2026-08-27T03:00:00Z",
        "market_close_time": "2026-08-27T03:00:00Z",
    }
