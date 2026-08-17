from src.polymarket_scope_guard import explicit_non_full_game, full_game_only_events, is_main_full_game_market


def market(slug, typ="SPORTS_MARKET_TYPE_SPREAD", line=-1.5, **extra):
    return {"slug":slug,"sportsMarketTypeV2":typ,"line":line,**extra}


def test_f5_slug_is_rejected():
    m=market("asc-mlb-bal-tb-2026-08-17-f5-neg-1pt5")
    assert explicit_non_full_game(m)
    assert not is_main_full_game_market({},m)


def test_first_five_text_is_rejected_even_without_f5_slug():
    m=market("bal-tb-special",question="Baltimore first 5 innings -1.5")
    assert not is_main_full_game_market({},m)


def test_main_spread_line_is_accepted_and_alt_line_rejected_when_event_exposes_main():
    event={"mainSpreadLine":-1.5}
    assert is_main_full_game_market(event,market("bal-tb-spread",line=-1.5))
    assert not is_main_full_game_market(event,market("bal-tb-alt-spread",line=-2.5))


def test_main_total_line_is_accepted():
    event={"mainTotalLine":8.5}
    m=market("bal-tb-total",typ="SPORTS_MARKET_TYPE_TOTAL",line=8.5,question="Over 8.5 runs")
    assert is_main_full_game_market(event,m)


def test_filter_removes_f5_market():
    events=[{"mainSpreadLine":-1.5,"markets":[market("full",line=-1.5),market("game-f5-neg-1pt5",line=-1.5)]}]
    out=full_game_only_events(events)
    assert [m["slug"] for m in out[0]["markets"]]==["full"]
