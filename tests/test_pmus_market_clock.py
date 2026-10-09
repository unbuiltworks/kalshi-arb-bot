from src import pmus_market_clock as MC


def test_pmus_crypto_datestamp_slug_resolves():
    dur, st, basis = MC.resolve("cpc-btc-updown-15m-2026-09-23-1345z")
    assert basis == "slug_datestamp"
    assert dur == 900.0
    # 2026-09-23 13:45Z
    assert st == 1790516700.0 or st > 0


def test_historical_unix_slug_still_resolves_the_training_way():
    dur, st, basis = MC.resolve("btc-up-or-down-5m-1750000000")
    assert (dur, st, basis) == (300.0, 1750000000.0, "slug_unix")


def test_venue_window_wins_over_slug():
    dur, st, basis = MC.resolve(
        "cpc-btc-updown-15m-2026-09-23-1345z",
        {"startTime": "2026-09-23T13:45:00Z", "endDate": "2026-09-23T14:00:00Z"})
    assert basis == "venue_window" and dur == 900.0


def test_unknown_slug_is_unresolved_not_fifteen_minutes():
    dur, st, basis = MC.resolve("aec-nfl-sf-lar-2026-09-10")
    assert (dur, st, basis) == (None, None, "UNRESOLVED")
    pos = MC.position("aec-nfl-sf-lar-2026-09-10", None, 1.0)
    assert pos["resolved"] is False and pos["t_frac"] is None


def test_fourteen_day_contract_expiry_is_not_a_clock():
    # endDate alone, 14 days out, must not become a duration
    dur, st, basis = MC.resolve("aec-nfl-sf-lar-2026-09-10",
                                {"endDate": "2026-09-24T23:30:00Z"})
    assert basis == "UNRESOLVED"


def test_position_matches_training_definition():
    pos = MC.position("btc-up-or-down-5m-1000000", None, 1000150.0)
    assert pos["t_frac"] == 0.5 and pos["tte_s"] == 150.0


def test_start_date_alias_is_accepted_but_a_seven_month_span_is_not():
    # measured on the live slate 2026-10-09: sports futures serve startDate
    # months before a contract endDate. That is not a window.
    dur, st, basis = MC.resolve("tec-mlb-nlchamp-2026-09-27-lad",
                                {"startDate": "2026-03-19T14:32:23Z",
                                 "endDate": "2026-11-06T21:20:09Z",
                                 "gameStartTime": "2026-09-07T00:00:00Z"})
    assert basis == "UNRESOLVED"
    # but a real 15-minute window served as startDate resolves
    dur, st, basis = MC.resolve("cpc-btc-updown-15m-2026-10-09-0215z",
                                {"startDate": "2026-10-09T02:15:00Z",
                                 "endDate": "2026-10-09T02:30:00Z"})
    assert (dur, basis) == (900.0, "venue_window")


def test_sports_clock_comes_from_event_progress_not_contract_expiry():
    prog = {"game_proximity": 0.75, "sport_type": "football",
            "proximity_basis": "period_token/quarters/football",
            "period": "Q4", "ended": False, "league": "nfl",
            "rebate_eligible": True, "fee_coefficient": 0.01}
    pos = MC.position("aec-nfl-sf-lar-2026-09-10",
                      {"startDate": "2026-03-19T14:32:23Z",
                       "endDate": "2026-11-06T21:20:09Z"},
                      1.0, progress=prog)
    assert pos["resolved"] is True
    assert pos["basis"] == "venue_event_progress"
    assert pos["t_frac"] == 0.75
    assert pos["tte_s_basis"] == "ASSUMED_NOMINAL_DURATION"
    assert pos["tte_s"] == 0.25 * 10800.0
    assert pos["CONTRACT_EXPIRY_NOT_USED"] is True
    assert pos["league"] == "nfl" and pos["rebate_eligible"] is True


def test_sports_clock_refuses_an_unrecognised_period():
    for prog in ({"game_proximity": None,
                  "proximity_basis": "unknown_series_length"},
                 {"game_proximity": 1.0, "ended": True},
                 None):
        pos = MC.position("aec-nfl-a-b-2026-09-10", None, 1.0, progress=prog)
        assert pos["resolved"] is False


def test_crypto_clock_is_unaffected_by_a_progress_row():
    pos = MC.position("cpc-btc-updown-15m-2026-09-23-1345z", None,
                      1790171100.0 + 450, progress={"game_proximity": 0.1})
    assert pos["basis"] == "slug_datestamp" and pos["t_frac"] == 0.5
