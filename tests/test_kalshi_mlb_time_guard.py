from src.kalshi_mlb_time_guard import ticker_start_utc


def test_august_ticker_clock_is_eastern_daylight_time():
    dt=ticker_start_utc("KXMLBGAME-26AUG171840MIAPHI")
    assert dt is not None
    assert dt.isoformat()=="2026-08-17T22:40:00+00:00"


def test_january_ticker_clock_is_eastern_standard_time():
    dt=ticker_start_utc("KXMLBGAME-26JAN171840MIAPHI")
    assert dt is not None
    assert dt.isoformat()=="2026-01-17T23:40:00+00:00"
