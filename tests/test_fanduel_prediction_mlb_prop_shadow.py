from decimal import Decimal
from types import SimpleNamespace

from src.fanduel_prediction_mlb_prop_shadow import (
    FanDuelLeg,
    discrete_threshold,
    evaluate,
    normalize_fanduel_event,
)

D=Decimal


def _event():
    return {
        "id":"evt1",
        "home_team":"Cincinnati Reds",
        "away_team":"St. Louis Cardinals",
        "commence_time":"2026-08-20T16:41:00Z",
        "bookmakers":[{
            "key":"fanduel",
            "markets":[{
                "key":"batter_hits_alternate",
                "last_update":"2026-08-20T12:32:53Z",
                "outcomes":[
                    {"name":"Over","description":"Elly De La Cruz","point":1.5,"price":3.0},
                    {"name":"Under","description":"Elly De La Cruz","point":1.5,"price":1.4},
                ],
            }],
        }],
    }


def test_half_point_discrete_thresholds_are_exact_and_whole_lines_reject():
    assert discrete_threshold(D("0.5")) == D("1")
    assert discrete_threshold(D("1.5")) == D("2")
    assert discrete_threshold(D("4.5")) == D("5")
    assert discrete_threshold(D("4.0")) is None


def test_over_and_under_share_exact_canonical_settlement_key():
    rows=normalize_fanduel_event(_event())
    assert len(rows)==2
    assert {row.side for row in rows}=={"YES","NO"}
    assert {row.threshold for row in rows}=={D("2")}
    assert len({row.settlement_key for row in rows})==1
    assert all(row.family=="baseball_player_hits" for row in rows)


def test_positive_manual_yes_compares_only_to_prediction_no():
    leg=FanDuelLeg(
        settlement_key="key",family="baseball_player_hits",player="Test Player",stat="hits",
        threshold=D("2"),side="YES",decimal_odds=D("2.5"),cost_per_dollar=D("0.4"),
        market_key="batter_hits_alternate",event_id="evt",event_start_ms=1000,quote_ms=2000,
    )
    quote=SimpleNamespace(
        settlement_key="key",venue="kalshi",market_id="k#YES",
        yes_cost=D("0.60"),no_cost=D("0.55"),yes_depth=D("1"),no_depth=D("1"),received_ms=2050,
    )
    result=evaluate([leg],[SimpleNamespace(quote=quote)],{"key":1000})
    assert result["positive_shadow_observations"]==1
    top=result["top_positive"][0]
    assert top["prediction_side"]=="NO"
    assert D(top["shadow_profit_per_dollar"])==D("0.05")
    assert top["manual_sportsbook_leg"] is True


def test_game_start_mismatch_fails_closed():
    leg=FanDuelLeg(
        settlement_key="key",family="baseball_player_hits",player="Test Player",stat="hits",
        threshold=D("2"),side="YES",decimal_odds=D("2.5"),cost_per_dollar=D("0.4"),
        market_key="batter_hits_alternate",event_id="evt",event_start_ms=1000,quote_ms=2000,
    )
    quote=SimpleNamespace(
        settlement_key="key",venue="kalshi",market_id="k#YES",
        yes_cost=D("0.60"),no_cost=D("0.55"),yes_depth=D("1"),no_depth=D("1"),received_ms=2050,
    )
    result=evaluate([leg],[SimpleNamespace(quote=quote)],{"key":1000 + 5*60*1000 + 1})
    assert result["fanduel_legs_with_exact_prediction_key"]==0
    assert result["positive_shadow_observations"]==0
