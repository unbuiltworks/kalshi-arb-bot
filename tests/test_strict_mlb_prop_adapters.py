from decimal import Decimal

import pytest

from src.strict_mlb_prop_adapters import (
    FAMILY_SERIES,
    RuleAuditEvidence,
    strict_match,
    supported_families,
)


def audit(series: str, *, equivalent: bool = True) -> RuleAuditEvidence:
    return RuleAuditEvidence(
        kalshi_series=series,
        kalshi_settlement_sources=("MLB official stats",),
        kalshi_contract_terms_url="https://example.test/kalshi-terms",
        polymarket_rules_ref="poly-rule-ref:test",
        kalshi_rule_summary="Settles from official MLB statistics for the named player/stat/game.",
        polymarket_rule_summary="Settles from official MLB statistics for the named player/stat/game.",
        equivalent=equivalent,
    )


def poly(family: str, stat_code: str, stat_text: str, line: str = "2") -> dict:
    return {
        "sportsMarketType": family,
        "slug": f"astatc-mlb-wsh-nym-2026-08-16-{stat_code}-fralin-gte{line}",
        "question": f"Will Francisco Lindor record at least {line} {stat_text} in WSH vs NYM?",
        "line": line,
    }


def poly_current_shape(
    family: str,
    slug_stat: str,
    stat_text: str,
    line: str = "2",
) -> dict:
    return {
        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_PROP",
        "slug": f"player-prop-mlb-wsh-nym-2026-08-16-{slug_stat}-francisco-lindor-gte{line}",
        "title": f"Francisco Lindor: {line}+ {stat_text}?",
        "line": line,
    }


def kalshi(series: str, title_stat: str, market_stat: str, line: str = "2") -> tuple[dict, dict]:
    event = {
        "series_ticker": series,
        "event_ticker": f"{series}-26AUG161340WSHNYM",
        "title": f"Washington vs New York M: {title_stat}",
    }
    market = {
        "ticker": f"{series}-26AUG161340WSHNYM-NYMFLINDOR12-{line}",
        "event_ticker": event["event_ticker"],
        "yes_sub_title": f"Francisco Lindor: {line}+ {market_stat}?",
    }
    return event, market


@pytest.mark.parametrize(
    "family,stat_code,poly_stat,series,title_stat,market_stat",
    [
        ("baseball_player_hits", "hits", "hits", "KXMLBHIT", "Hits", "hits"),
        (
            "baseball_player_hits_runs_rbis",
            "hrr",
            "hits + runs + RBIs",
            "KXMLBHRR",
            "Hits + Runs + RBIs",
            "hits + runs + RBIs",
        ),
        (
            "baseball_player_total_bases",
            "tb",
            "total bases",
            "KXMLBTB",
            "Total Bases",
            "total bases",
        ),
        (
            "baseball_player_strikeouts",
            "k",
            "pitching strikeouts",
            "KXMLBKS",
            "Strikeouts",
            "strikeouts",
        ),
    ],
)
def test_strict_family_match(
    family: str,
    stat_code: str,
    poly_stat: str,
    series: str,
    title_stat: str,
    market_stat: str,
) -> None:
    p = poly(family, stat_code, poly_stat)
    event, market = kalshi(series, title_stat, market_stat)
    result = strict_match(p, event, market, audit(series))
    assert result is not None
    assert result.family == family
    assert result.threshold == Decimal("2")
    assert result.settlement_confidence == "FULL_RULES_AUDITED_SHADOW"
    assert result.read_only is True
    assert result.order_entry is False
    assert result.settlement_key.startswith("mlb:2026-08-16:nym-wsh:")


@pytest.mark.parametrize(
    "family,slug_stat,poly_stat,series,title_stat,market_stat",
    [
        ("baseball_player_hits", "hits", "hits", "KXMLBHIT", "Hits", "hits"),
        (
            "baseball_player_hits_runs_rbis",
            "hrr",
            "hits + runs + RBIs",
            "KXMLBHRR",
            "Hits + Runs + RBIs",
            "hits + runs + RBIs",
        ),
        (
            "baseball_player_total_bases",
            "tb",
            "total bases",
            "KXMLBTB",
            "Total Bases",
            "total bases",
        ),
        (
            "baseball_player_strikeouts",
            "strikeouts",
            "strikeouts",
            "KXMLBKS",
            "Strikeouts",
            "strikeouts",
        ),
    ],
)
def test_current_poly_prop_shape_matches_strictly(
    family: str,
    slug_stat: str,
    poly_stat: str,
    series: str,
    title_stat: str,
    market_stat: str,
) -> None:
    p = poly_current_shape(family, slug_stat, poly_stat)
    event, market = kalshi(series, title_stat, market_stat)
    result = strict_match(p, event, market, audit(series))
    assert result is not None
    assert result.family == family
    assert result.threshold == Decimal("2")


def test_current_shape_rejects_prop_family_disagreement() -> None:
    p = poly_current_shape("baseball_player_hits", "hits", "total bases", "2")
    event, market = kalshi("KXMLBHIT", "Hits", "hits", "2")
    assert strict_match(p, event, market, audit("KXMLBHIT")) is None


def test_current_shape_rejects_threshold_disagreement() -> None:
    p = poly_current_shape("baseball_player_total_bases", "tb", "total bases", "2")
    p["line"] = "3"
    event, market = kalshi("KXMLBTB", "Total Bases", "total bases", "2")
    assert strict_match(p, event, market, audit("KXMLBTB")) is None


def test_rejects_hits_to_hrr_false_positive() -> None:
    p = poly("baseball_player_hits", "hits", "hits")
    event, market = kalshi("KXMLBHRR", "Hits + Runs + RBIs", "hits + runs + RBIs")
    assert strict_match(p, event, market, audit("KXMLBHRR")) is None


def test_rejects_homer_to_hrr_family_entirely() -> None:
    p = {
        "sportsMarketType": "baseball_player_home_runs",
        "slug": "astatc-mlb-wsh-nym-2026-08-16-hr-fralin-gte1",
        "question": "Will Francisco Lindor record at least 1 home runs in WSH vs NYM?",
        "line": "1",
    }
    event, market = kalshi("KXMLBHRR", "Hits + Runs + RBIs", "hits + runs + RBIs", "1")
    assert strict_match(p, event, market, audit("KXMLBHRR")) is None


def test_rejects_threshold_mismatch() -> None:
    p = poly("baseball_player_total_bases", "tb", "total bases", "3")
    event, market = kalshi("KXMLBTB", "Total Bases", "total bases", "4")
    assert strict_match(p, event, market, audit("KXMLBTB")) is None


def test_rejects_player_mismatch() -> None:
    p = poly("baseball_player_strikeouts", "k", "pitching strikeouts", "5")
    event, market = kalshi("KXMLBKS", "Strikeouts", "strikeouts", "5")
    market["yes_sub_title"] = "Jacob deGrom: 5+ strikeouts?"
    assert strict_match(p, event, market, audit("KXMLBKS")) is None


def test_rejects_event_mismatch() -> None:
    p = poly("baseball_player_hits_runs_rbis", "hrr", "hits + runs + RBIs", "2")
    event, market = kalshi("KXMLBHRR", "Hits + Runs + RBIs", "hits + runs + RBIs", "2")
    event["event_ticker"] = "KXMLBHRR-26AUG161340PHIMIN"
    market["event_ticker"] = event["event_ticker"]
    assert strict_match(p, event, market, audit("KXMLBHRR")) is None


def test_rejects_missing_rule_audit() -> None:
    p = poly("baseball_player_hits", "hits", "hits")
    event, market = kalshi("KXMLBHIT", "Hits", "hits")
    assert strict_match(p, event, market, None) is None


def test_rejects_negative_rule_audit() -> None:
    p = poly("baseball_player_hits", "hits", "hits")
    event, market = kalshi("KXMLBHIT", "Hits", "hits")
    assert strict_match(p, event, market, audit("KXMLBHIT", equivalent=False)) is None


def test_rejects_audit_for_wrong_series() -> None:
    p = poly("baseball_player_total_bases", "tb", "total bases")
    event, market = kalshi("KXMLBTB", "Total Bases", "total bases")
    assert strict_match(p, event, market, audit("KXMLBHIT")) is None


def test_rejects_question_slug_family_disagreement() -> None:
    p = poly("baseball_player_hits", "hits", "hits")
    p["question"] = "Will Francisco Lindor record at least 2 total bases in WSH vs NYM?"
    event, market = kalshi("KXMLBHIT", "Hits", "hits")
    assert strict_match(p, event, market, audit("KXMLBHIT")) is None


def test_supported_families_are_explicit() -> None:
    assert supported_families() == dict(FAMILY_SERIES)
    assert supported_families() == {
        "baseball_player_hits": "KXMLBHIT",
        "baseball_player_hits_runs_rbis": "KXMLBHRR",
        "baseball_player_total_bases": "KXMLBTB",
        "baseball_player_strikeouts": "KXMLBKS",
    }
