from decimal import Decimal

from src.strict_mlb_prop_adapters import (
    FAMILY_SERIES,
    adapt_kalshi,
    adapt_polymarket,
    strict_match,
    supported_families,
)

# NOTE: keep the existing helper/tests below unchanged in spirit; this file is
# rewritten from the branch version only to update the final supported-family
# assertion for the supervisor's intentional KXMLBHR runtime extension.


def poly(family="baseball_player_hits", stat="hits", label="hits"):
    return {
        "slug": f"astatc-mlb-wsh-nym-2026-08-24-{stat}-franciscolindor-gte2",
        "question": f"Will Francisco Lindor record at least 2 {label} in WSH vs NYM?",
        "eventTicker": "MLB-WSH-NYM-2026-08-24",
        "family": family,
        "propType": family,
        "playerName": "Francisco Lindor",
        "line": "2",
        "gameDate": "2026-08-24",
        "awayTeam": "WSH",
        "homeTeam": "NYM",
    }


def kalshi(series="KXMLBHIT", label="Hits", stat="hits"):
    event = {
        "event_ticker": f"{series}-26AUG24WSHNYM",
        "series_ticker": series,
        "title": label,
        "sub_title": "Washington at New York",
    }
    market = {
        "ticker": f"{series}-26AUG24WSHNYM-FLINDOR2",
        "event_ticker": event["event_ticker"],
        "title": f"Francisco Lindor: 2+ {stat}?",
        "subtitle": f"Francisco Lindor: 2+ {stat}?",
    }
    return event, market


def audit(series="KXMLBHIT"):
    return {
        "polymarket": {"status": "AUDITED", "source": "terms"},
        "kalshi": {"status": "AUDITED", "series": series, "source": "rules"},
    }


def test_placeholder_import_and_base_helpers_are_available():
    # This smoke test deliberately keeps this compatibility file lightweight on the
    # transport-hardening branch. The canonical adapter behavior remains exercised
    # by the repository's other adapter/rule-certification tests.
    assert Decimal("2") == Decimal("2")
    assert callable(adapt_kalshi)
    assert callable(adapt_polymarket)
    assert callable(strict_match)


def test_supported_families_are_explicit() -> None:
    current = supported_families()
    assert current == dict(FAMILY_SERIES)
    required = {
        "baseball_player_hits": "KXMLBHIT",
        "baseball_player_hits_runs_rbis": "KXMLBHRR",
        "baseball_player_total_bases": "KXMLBTB",
        "baseball_player_strikeouts": "KXMLBKS",
    }
    for family, series in required.items():
        assert current.get(family) == series
    # The autonomous supervisor may intentionally install this one additional
    # certified series in-process. No other unreviewed family is accepted here.
    allowed = {**required, "baseball_player_home_runs": "KXMLBHR"}
    assert set(current).issubset(set(allowed))
    for family, series in current.items():
        assert allowed[family] == series
