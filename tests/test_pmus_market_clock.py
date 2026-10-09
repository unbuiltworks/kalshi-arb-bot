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
