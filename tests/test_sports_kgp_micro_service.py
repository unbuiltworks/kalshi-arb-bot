from decimal import Decimal as D

from src.sports_kgp_micro_guard import RuleApproval
from src.sports_kgp_micro_service import candidate_plans


KEY = "baseball|mlb|player_prop|game|player|john-doe|hits|GE|1"
NOW = 1_800_000_000_000


def approval():
    return RuleApproval(
        settlement_key=KEY,
        venue_a="kalshi", market_a="KX", source_side_a="YES",
        venue_b="gemini", market_b="GEMI", source_side_b="YES",
        rule_fingerprint_sha256="a" * 64,
        approved_for_execution=True,
    )


def quote(venue, market, yes, no, received, depth_source):
    return {
        "venue": venue,
        "market_id": market + "#YES",
        "settlement_key": KEY,
        "raw_yes_ask": yes,
        "raw_no_ask": no,
        "yes_fee": "0",
        "no_fee": "0",
        "all_in_yes_cost": yes,
        "all_in_no_cost": no,
        "yes_depth": "1",
        "no_depth": "1",
        "received_ms": received,
        "depth_source": depth_source,
        "source_side": "YES",
        "settlement_confidence": "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    }


def result(gemini_no="0.57", *, assessment_exec=True, lock_conf="FULL_RULES_AUDITED_EXECUTABLE"):
    return {
        "top_one_contract_locks": [{
            "settlement_key": KEY,
            "category": "sports_mlb_prop",
            "yes_venue": "kalshi",
            "yes_market_id": "KX#YES",
            "no_venue": "gemini",
            "no_market_id": "GEMI#YES",
            "unit_cost": "0.95",
            "unit_profit": "0.05",
            "max_quantity": "1",
            "max_capital": "0.95",
            "max_locked_profit": "0.05",
            "quote_skew_ms": 10,
            "settlement_confidence": lock_conf,
            "expected_settlement_minutes": 180,
            "capital_velocity_score": "1",
        }],
        "positive_quote_audit": [
            quote("kalshi", "KX", "0.40", "0.60", NOW - 20, "kalshi_public_orderbook_depth1_final"),
            quote("gemini", "GEMI", "0.43", gemini_no, NOW - 10, "gemini_depth10_100ms_final"),
        ],
        "rule_audit": {"top_assessments": [{
            "settlement_key": KEY,
            "execution_eligible": assessment_exec,
        }]},
    }


def test_final_book_recomputes_profit_instead_of_reusing_discovery_lock():
    plans, rejected = candidate_plans(result("0.57"), {KEY: [approval()]}, now_ms=NOW)
    assert rejected == []
    assert len(plans) == 1
    plan, _ = plans[0]
    assert plan.unit_cost == D("0.97")
    assert plan.locked_profit == D("0.03")


def test_final_book_that_loses_edge_cannot_execute_even_if_discovery_said_5c():
    plans, rejected = candidate_plans(result("0.59"), {KEY: [approval()]}, now_ms=NOW)
    assert plans == []
    assert any("no longer contain" in row["reason"] for row in rejected)


def test_rule_assessment_must_independently_be_execution_eligible():
    plans, rejected = candidate_plans(result(assessment_exec=False), {KEY: [approval()]}, now_ms=NOW)
    assert plans == []
    assert rejected[0]["reason"] == "rule_assessment_not_execution_eligible"


def test_scanner_lock_must_independently_be_execution_audited():
    plans, rejected = candidate_plans(
        result(lock_conf="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"),
        {KEY: [approval()]},
        now_ms=NOW,
    )
    assert plans == []
    assert rejected[0]["reason"] == "scanner_lock_not_execution_audited"


def test_discovery_only_gemini_row_remains_blocked_even_after_rule_approval():
    body = result("0.57")
    body["positive_quote_audit"][1]["depth_source"] = "event_prices_q1_probe_only"
    plans, rejected = candidate_plans(body, {KEY: [approval()]}, now_ms=NOW)
    assert plans == []
    assert any("approved final execution book" in row["reason"] for row in rejected)
