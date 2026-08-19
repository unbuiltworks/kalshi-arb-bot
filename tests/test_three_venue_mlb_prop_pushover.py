import asyncio

from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE
from src.three_venue_mlb_prop_supervisor import _deliver_prop_alerts, _notification_candidates


def _result(*, executable: bool, confidence: str):
    key = "baseball|mlb|nym,phi|1787079600000|player_prop|full_contest|player|rhyshoskins|total_bases|GE|2"
    return {
        "top_one_contract_locks": [{
            "settlement_key": key,
            "category": "sports_mlb_prop",
            "yes_venue": "polymarket",
            "yes_market_id": "poly#YES",
            "no_venue": "kalshi",
            "no_market_id": "kalshi#YES",
            "unit_cost": "0.95",
            "unit_profit": "0.05",
            "max_quantity": "1",
            "max_capital": "0.95",
            "max_locked_profit": "0.05",
            "quote_skew_ms": 100,
            "settlement_confidence": confidence,
            "expected_settlement_minutes": None,
            "capital_velocity_score": "0.05",
        }],
        "positive_quote_audit": [
            {
                "venue": "polymarket",
                "market_id": "poly#YES",
                "settlement_key": key,
                "all_in_yes_cost": "0.45",
                "all_in_no_cost": "0.56",
                "yes_fee": "0.00",
                "no_fee": "0.00",
                "received_ms": 1787079600000,
            },
            {
                "venue": "kalshi",
                "market_id": "kalshi#YES",
                "settlement_key": key,
                "all_in_yes_cost": "0.50",
                "all_in_no_cost": "0.50",
                "yes_fee": "0.00",
                "no_fee": "0.00",
                "received_ms": 1787079600100,
            },
        ],
        "rule_audit": {
            "top_assessments": [{
                "settlement_key": key,
                "status": "CERTIFIED" if executable else "UNAUDITED",
                "execution_eligible": executable,
                "player": "Rhys Hoskins",
                "family": "baseball_player_total_bases",
            }]
        },
    }


def test_notification_adapter_requires_both_certification_signals():
    rows = _notification_candidates(_result(executable=True, confidence=ACTIONABLE_CONFIDENCE))
    assert len(rows) == 1
    lock, quotes, assessment, actionable = rows[0]
    assert actionable is True
    assert assessment["execution_eligible"] is True
    assert len(quotes) == 2
    assert lock.settlement_confidence == ACTIONABLE_CONFIDENCE

    rows = _notification_candidates(_result(executable=True, confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"))
    assert rows[0][3] is False
    rows = _notification_candidates(_result(executable=False, confidence=ACTIONABLE_CONFIDENCE))
    assert rows[0][3] is False


class _FakeSink:
    configured = True

    def __init__(self):
        self.calls = []

    async def maybe_send(
        self,
        lock,
        rows,
        *,
        rule_status,
        source,
        actionable,
        display_title=None,
        display_market=None,
    ):
        self.calls.append({
            "lock": lock,
            "rows": list(rows),
            "rule_status": rule_status,
            "source": source,
            "actionable": actionable,
            "display_title": display_title,
            "display_market": display_market,
        })
        return True


def test_delivery_never_calls_sink_for_uncertified_lock():
    sink = _FakeSink()
    status = asyncio.run(_deliver_prop_alerts(
        _result(executable=False, confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"),
        sink,
    ))
    assert sink.calls == []
    assert status["eligible_this_scan"] == 0
    assert status["blocked_by_rule_or_settlement_gate"] == 1
    assert status["alerts_sent_this_scan"] == 0


def test_delivery_calls_sink_with_human_readable_identity_for_certified_lock():
    sink = _FakeSink()
    status = asyncio.run(_deliver_prop_alerts(
        _result(executable=True, confidence=ACTIONABLE_CONFIDENCE),
        sink,
    ))
    assert len(sink.calls) == 1
    call = sink.calls[0]
    assert call["display_market"] == "Rhys Hoskins — 2+ Total Bases"
    assert call["display_title"] == "Rhys Hoskins • 2+ Total Bases • CERTIFIED ARB"
    assert call["actionable"] is True
    assert status["eligible_this_scan"] == 1
    assert status["alerts_sent_this_scan"] == 1
    assert status["alert_errors_this_scan"] == 0
