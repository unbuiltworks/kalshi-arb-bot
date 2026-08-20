from decimal import Decimal

from src.nfl_identity_probe import NflIdentity
from src.nfl_shadow_price_probe import Quote, _clusters, scan

D=Decimal


def row(venue, market_id, start_ms, family="moneyline", subject="PHI"):
    return NflIdentity(
        venue=venue, market_id=market_id, family=family, teams=("DAL","PHI"),
        subject=subject, comparator="YES" if family=="moneyline" else "GT",
        line=None if family=="moneyline" else D("2.5"),
        start_ms=start_ms, start_provenance="test",
    )


def test_same_teams_rematch_months_later_never_merges():
    first=1_000_000
    later=first+60*24*60*60*1000
    rows=[
        row("gemini","g1",first),row("kalshi","k1",first),
        row("gemini","g2",later),row("kalshi","k2",later),
    ]
    clusters=_clusters(rows)
    assert len(clusters)==2
    assert {x.market_id for x in clusters[0]}=={"g1","k1"}
    assert {x.market_id for x in clusters[1]}=={"g2","k2"}


def test_positive_gemini_sportsbook_snapshot_remains_unaudited_not_executable():
    start=1_000_000
    rows=[row("gemini","g",start),row("sportsbook:fanduel","fd",start)]
    quotes={
        ("gemini","g"):Quote("gemini","g",D("0.40"),D("0.60")),
        ("sportsbook:fanduel","fd"):Quote("sportsbook:fanduel","fd",D("0.60"),D("0.50"),source="test"),
    }
    result=scan(rows,quotes,{})
    assert result["positive_observations"]>=1
    candidate=result["top_positive"][0]
    assert candidate["shadow_profit"]>"0"
    assert candidate["rule_status"]=="UNAUDITED"
    assert candidate["rule_executable"] is False
    assert candidate["execution_status"]=="SHADOW_ONLY_NO_DEPTH_NO_FRESHNESS_CERTIFICATION"


def test_gemini_kalshi_moneyline_positive_is_conditional_due_rule_mismatch():
    # Use sportsbook on one side too because Kalshi fee metadata is deliberately
    # absent in this unit test; direct G/K live math is exercised only when fee
    # metadata has been fetched successfully in the live probe.
    start=1_000_000
    rows=[row("gemini","g",start),row("sportsbook:draftkings","dk",start)]
    quotes={
        ("gemini","g"):Quote("gemini","g",D("0.35"),D("0.65")),
        ("sportsbook:draftkings","dk"):Quote("sportsbook:draftkings","dk",D("0.70"),D("0.50"),source="test"),
    }
    result=scan(rows,quotes,{})
    assert all(x["rule_executable"] is False for x in result["top_positive"])
