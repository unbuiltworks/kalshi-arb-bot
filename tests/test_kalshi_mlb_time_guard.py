from src.kalshi_mlb_strict_adapter import normalize_kalshi_mlb_strict
from src.kalshi_mlb_time_guard import ticker_start_utc


def test_august_ticker_clock_is_eastern_daylight_time():
    dt=ticker_start_utc("KXMLBGAME-26AUG171840MIAPHI")
    assert dt is not None
    assert dt.isoformat()=="2026-08-17T22:40:00+00:00"


def test_january_ticker_clock_is_eastern_standard_time():
    dt=ticker_start_utc("KXMLBGAME-26JAN171840MIAPHI")
    assert dt is not None
    assert dt.isoformat()=="2026-01-17T23:40:00+00:00"


def test_moneyline_yes_team_comes_from_yes_sub_title_not_broad_event_title():
    events=[{
        "series_ticker":"KXMLBGAME",
        "event_ticker":"KXMLBGAME-26AUG171805BALTB",
        "title":"Baltimore vs Tampa Bay Winner?",
        "markets":[
            {"ticker":"KXMLBGAME-26AUG171805BALTB-BAL","title":"Baltimore vs Tampa Bay Winner?","yes_sub_title":"Baltimore","no_sub_title":"Baltimore","expected_expiration_time":"2026-08-18T01:05:00Z"},
            {"ticker":"KXMLBGAME-26AUG171805BALTB-TB","title":"Baltimore vs Tampa Bay Winner?","yes_sub_title":"Tampa Bay","no_sub_title":"Tampa Bay","expected_expiration_time":"2026-08-18T01:05:00Z"},
        ],
    }]
    rows=normalize_kalshi_mlb_strict(events)
    subjects={(row.venue_market_id,row.side):row.identity.subject for row in rows}
    assert subjects[("KXMLBGAME-26AUG171805BALTB-BAL","YES")]=="bal"
    assert subjects[("KXMLBGAME-26AUG171805BALTB-BAL","NO")]=="tb"
    assert subjects[("KXMLBGAME-26AUG171805BALTB-TB","YES")]=="tb"
    assert subjects[("KXMLBGAME-26AUG171805BALTB-TB","NO")]=="bal"
