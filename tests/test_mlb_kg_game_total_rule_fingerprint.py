import hashlib

import pytest

from src.mlb_kg_game_total_rule_fingerprint import (
    build_pair_fingerprint,
    find_gemini_contract,
    gemini_rule_text,
    kalshi_rule_text,
)


KEY = "baseball|mlb|az,bos|game_total|full_contest|contest|contest|runs|GT|12.5"


def _kalshi():
    return {
        "ticker": "KXMLBTOTAL-26AUG182010AZBOS-13",
        "title": "Arizona vs Boston Total Runs?",
        "rules_primary": "If Arizona and Boston collectively score more than 12.5 runs, resolves Yes.",
        "rules_secondary": "Official market rules apply.",
        "early_close_condition": "This market may close when the outcome is determined.",
    }


def _gemini():
    return {
        "instrumentSymbol": "GEMI-MLB-2608190010-ARI-BOS-T-O12",
        "label": "More than 12.5 runs scored",
        "termsAndConditionsUrl": "https://assets.gemini.com/predictions/terms_and_conditions/total.pdf",
        "description": {
            "nodeType": "document",
            "content": [
                {
                    "nodeType": "paragraph",
                    "content": [
                        {
                            "nodeType": "text",
                            "value": "This market resolves Yes if Arizona and Boston collectively score over 12.5 runs.",
                        }
                    ],
                },
                {
                    "nodeType": "paragraph",
                    "content": [
                        {
                            "nodeType": "text",
                            "value": "If the result cannot be determined, Market Outcome Review may apply.",
                        }
                    ],
                },
            ],
        },
    }


def test_rule_text_extraction_is_stable_and_nonempty():
    assert "12.5" in kalshi_rule_text(_kalshi())
    g_text = gemini_rule_text(_gemini())
    assert "12.5" in g_text
    assert "Market Outcome Review" in g_text


def test_exact_pair_fingerprint_is_deterministic_and_never_self_approves():
    a = build_pair_fingerprint(
        settlement_key=KEY,
        kalshi_market=_kalshi(),
        gemini_contract=_gemini(),
        kalshi_source_side="YES",
        gemini_source_side="YES",
    )
    b = build_pair_fingerprint(
        settlement_key=KEY,
        kalshi_market=_kalshi(),
        gemini_contract=_gemini(),
        kalshi_source_side="YES",
        gemini_source_side="YES",
    )
    assert a.rule_fingerprint_sha256 == b.rule_fingerprint_sha256
    assert len(a.rule_fingerprint_sha256) == 64
    int(a.rule_fingerprint_sha256, 16)
    assert a.approval_candidate["approved_for_execution"] is False
    assert a.approval_candidate["market_a"] == _kalshi()["ticker"]
    assert a.approval_candidate["market_b"] == _gemini()["instrumentSymbol"]
    assert a.read_only is True
    assert a.order_entry is False


def test_current_real_kalshi_gemini_total_stays_fail_closed():
    result = build_pair_fingerprint(
        settlement_key=KEY,
        kalshi_market=_kalshi(),
        gemini_contract=_gemini(),
        kalshi_source_side="YES",
        gemini_source_side="NO",
    )
    assert result.certification_status == "CONDITIONAL"
    assert result.exact_settlement_equivalent is False
    assert result.execution_eligible is False
    assert "cancellation" in result.blockers
    assert "kalshi_complete_semantic_profile_missing" not in result.blockers
    assert "gemini_complete_semantic_profile_missing" not in result.blockers


def test_missing_exact_rule_text_adds_hard_blocker():
    market = {"ticker": _kalshi()["ticker"]}
    result = build_pair_fingerprint(
        settlement_key=KEY,
        kalshi_market=market,
        gemini_contract=_gemini(),
        kalshi_source_side="YES",
        gemini_source_side="YES",
    )
    assert "kalshi_exact_rule_text_missing" in result.blockers
    assert result.execution_eligible is False


def test_non_game_total_key_is_rejected():
    with pytest.raises(ValueError, match="game_total"):
        build_pair_fingerprint(
            settlement_key=KEY.replace("|game_total|", "|moneyline|"),
            kalshi_market=_kalshi(),
            gemini_contract=_gemini(),
            kalshi_source_side="YES",
            gemini_source_side="YES",
        )


def test_gemini_contract_lookup_requires_unique_exact_symbol():
    target = _gemini()
    payload = {"data": [{"events": [{"contracts": [target, {**target, "instrumentSymbol": "OTHER"}]}]}]}
    assert find_gemini_contract(payload, target["instrumentSymbol"]) == target
    duplicate = {"data": [{"contracts": [target, dict(target)]}]}
    assert find_gemini_contract(duplicate, target["instrumentSymbol"]) is None
