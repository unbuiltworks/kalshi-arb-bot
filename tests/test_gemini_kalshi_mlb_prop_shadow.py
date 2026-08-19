from src.gemini_kalshi_mlb_prop_shadow import _kalshi_teams


def test_kalshi_prop_event_ticker_decodes_exact_team_pair():
    event = {"event_ticker": "KXMLBKS-26AUG191210SFCLE"}
    assert _kalshi_teams(event) == ("CLE", "SF")


def test_kalshi_prop_event_ticker_handles_alias_team_codes():
    event = {"event_ticker": "KXMLBHIT-26AUG191905ARILAD"}
    assert _kalshi_teams(event) == ("AZ", "LAD")


def test_kalshi_prop_event_ticker_fails_closed_when_schedule_suffix_missing():
    assert _kalshi_teams({"event_ticker": "KXMLBHIT-NOT-A-GAME"}) is None
