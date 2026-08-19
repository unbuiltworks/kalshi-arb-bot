from src.mlb_kp_game_total_rule_fingerprint import (
    POLY_SPORTS_RULE_SOURCE,
    build_pair_fingerprint,
    semantic_blockers,
)


def _kalshi():
    return {
        "ticker": "KXMLBTOTAL-26AUG181940ATHKC-10",
        "rules_primary": "If A's and Kansas City collectively score more 9.5 runs, then Yes.",
        "rules_secondary": "",
        "early_close_condition": "This market will close and expire early if the event occurs.",
    }


def _poly():
    return {
        "slug": "tsc-mlb-ath-kc-2026-08-18-9pt5",
        "question": "Will the total score be over 9.5?",
        "description": "MLB total score market",
        "resolutionSource": "MLB",
        "sportsMarketTypeV2": "TOTAL",
        "line": 9.5,
    }


def test_real_venue_profiles_remain_non_equivalent():
    result = build_pair_fingerprint(
        settlement_key="baseball|mlb|ath,kc|game_total|full_contest|contest|contest|runs|GT|9.5",
        kalshi_market=_kalshi(),
        polymarket_market=_poly(),
    )
    assert result.exact_settlement_equivalent is False
    assert result.execution_eligible is False
    assert result.approval_candidate["approved_for_execution"] is False
    assert "not_started" in result.blockers
    assert "interrupted" in result.blockers
    assert "cancellation" in result.blockers
    assert result.venue_b.semantic_source == POLY_SPORTS_RULE_SOURCE


def test_exact_market_identity_is_bound_fail_closed():
    bad = _poly()
    bad["line"] = 10.5
    result = build_pair_fingerprint(
        settlement_key="baseball|mlb|ath,kc|game_total|full_contest|contest|contest|runs|GT|9.5",
        kalshi_market=_kalshi(),
        polymarket_market=bad,
    )
    assert "polymarket_exact_line_mismatch" in result.blockers

    bad = _poly()
    bad["sportsMarketTypeV2"] = "SPREAD"
    result = build_pair_fingerprint(
        settlement_key="baseball|mlb|ath,kc|game_total|full_contest|contest|contest|runs|GT|9.5",
        kalshi_market=_kalshi(),
        polymarket_market=bad,
    )
    assert "polymarket_exact_market_not_total" in result.blockers


def test_semantic_blockers_are_dimension_names_only():
    left = {
        "official_source": "a", "normal_outcome": "same", "extra_innings": "included",
        "minimum_completion": "a", "not_started": "a", "interrupted": "a",
        "cancellation": "a", "push_tie": "same",
    }
    right = dict(left)
    right["cancellation"] = "b"
    assert semantic_blockers(left, right) == ("cancellation",)


def test_non_game_total_key_is_rejected():
    try:
        build_pair_fingerprint(
            settlement_key="baseball|mlb|ath,kc|spread|full_contest|team|ath|runs|GT|1.5",
            kalshi_market=_kalshi(), polymarket_market=_poly(),
        )
    except ValueError as exc:
        assert "game_total" in str(exc)
    else:
        raise AssertionError("expected fail-closed rejection")
