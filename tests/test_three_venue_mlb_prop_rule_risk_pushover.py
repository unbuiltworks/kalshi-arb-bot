import asyncio
from types import SimpleNamespace

from src.three_venue_mlb_prop_supervisor import (
    RuleRiskPolicy,
    _deliver_rule_risk_alerts,
    _notification_candidates,
    _rule_risk_qualification,
)

NOW_MS = 1787079605000
KEY = "baseball|mlb|cle,sf|1787079600000|player_prop|full_contest|player|rafaeldevers|hits|GE|3"


def _result(*, participation=True, executable=False, yes_venue="polymarket", no_venue="kalshi", unit_profit="0.05"):
    return {
        "top_one_contract_locks": [{
            "settlement_key": KEY,
            "category": "sports_mlb_prop",
            "yes_venue": yes_venue,
            "yes_market_id": f"{yes_venue}#YES",
            "no_venue": no_venue,
            "no_market_id": f"{no_venue}#YES",
            "unit_cost": str(1 - float(unit_profit)),
            "unit_profit": unit_profit,
            "max_quantity": "1",
            "max_capital": str(1 - float(unit_profit)),
            "max_locked_profit": unit_profit,
            "quote_skew_ms": 100,
            "settlement_confidence": "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
            "expected_settlement_minutes": None,
            "capital_velocity_score": unit_profit,
        }],
        "positive_quote_audit": [
            {
                "venue": yes_venue,
                "market_id": f"{yes_venue}#YES",
                "settlement_key": KEY,
                "all_in_yes_cost": "0.45",
                "all_in_no_cost": "0.56",
                "yes_fee": "0.00",
                "no_fee": "0.00",
                "received_ms": NOW_MS - 500,
            },
            {
                "venue": no_venue,
                "market_id": f"{no_venue}#YES",
                "settlement_key": KEY,
                "all_in_yes_cost": "0.50",
                "all_in_no_cost": "0.50",
                "yes_fee": "0.00",
                "no_fee": "0.00",
                "received_ms": NOW_MS - 400,
            },
        ],
        "rule_audit": {
            "top_assessments": [{
                "settlement_key": KEY,
                "status": "CERTIFIED" if executable else "UNAUDITED",
                "execution_eligible": executable,
                "participation_proven": participation,
                "remaining_blockers": [] if executable else [
                    "cancellation",
                    "interruption",
                    "stat_corrections",
                    "complete_lifecycle_fingerprint_required",
                ],
            }]
        },
    }


def _candidate_parts(result):
    rows = _notification_candidates(result)
    assert len(rows) == 1
    return rows[0]


def test_rule_risk_requires_live_participation_and_direct_prediction_pair():
    policy = RuleRiskPolicy()
    lock, quotes, assessment, _ = _candidate_parts(_result(participation=False))
    ok, reason, _, _ = _rule_risk_qualification(lock, quotes, assessment, policy, now_ms=NOW_MS)
    assert ok is False
    assert reason == "participation_not_proven"

    lock, quotes, assessment, _ = _candidate_parts(_result(yes_venue="sportsbook:fanduel"))
    ok, reason, _, _ = _rule_risk_qualification(lock, quotes, assessment, policy, now_ms=NOW_MS)
    assert ok is False
    assert reason == "not_direct_prediction_pair"


def test_rule_risk_candidate_does_not_accept_certified_or_tiny_edge():
    policy = RuleRiskPolicy()
    lock, quotes, assessment, _ = _candidate_parts(_result(executable=True))
    ok, reason, _, _ = _rule_risk_qualification(lock, quotes, assessment, policy, now_ms=NOW_MS)
    assert ok is False
    assert reason == "certified_path_only"

    lock, quotes, assessment, _ = _candidate_parts(_result(unit_profit="0.01"))
    ok, reason, _, _ = _rule_risk_qualification(lock, quotes, assessment, policy, now_ms=NOW_MS)
    assert ok is False
    assert reason == "unit_profit_below_candidate_floor"


def test_rule_risk_direct_post_participation_candidate_qualifies_without_promoting_confidence():
    policy = RuleRiskPolicy()
    lock, quotes, assessment, actionable = _candidate_parts(_result())
    ok, reason, yes, no = _rule_risk_qualification(lock, quotes, assessment, policy, now_ms=NOW_MS)
    assert actionable is False
    assert lock.settlement_confidence == "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
    assert ok is True
    assert reason == "qualified_rule_risk_candidate"
    assert yes["venue"] == "polymarket"
    assert no["venue"] == "kalshi"


class _FakeSink:
    configured = True

    def __init__(self):
        self.messages = []

    async def send(self, *, title, message):
        self.messages.append((title, message))
        return "fake-request"


def test_rule_risk_delivery_is_explicitly_not_guaranteed_and_never_order_capable():
    sink = _FakeSink()
    status = asyncio.run(_deliver_rule_risk_alerts(
        _result(),
        sink=sink,
        policy=RuleRiskPolicy(),
        now_ms=NOW_MS,
    ))
    assert status["eligible_rule_risk_candidates"] == 1
    assert status["alerts_sent_this_scan"] == 1
    assert status["execution_eligible"] is False
    assert status["order_entry"] is False
    assert len(sink.messages) == 1
    title, message = sink.messages[0]
    assert "RULE RISK" in title
    assert "NOT GUARANTEED" in message
    assert "MLB participation: PROVEN" in message
    assert "no order entry" in message
