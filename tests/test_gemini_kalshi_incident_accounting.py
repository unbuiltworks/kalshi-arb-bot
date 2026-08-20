from decimal import Decimal

import pytest

from src import gemini_kalshi_incident_accounting as incident

D = Decimal


def _incident_record():
    return {
        "completed_at_utc": "2026-08-20T14:50:05.567Z",
        "gemini_payload": {"symbol": "GEMI-XRP15M2608201500-UP", "eventOutcome": "yes"},
        "kalshi_payload": {"ticker": "KXXRP15M-26AUG201100-00", "side": "ask", "price": "0.15"},
        "result": {
            "outcome": "ONE_LEG_FILLED",
            "hard_halt_required": True,
            "gemini": {
                "status": "FILLED",
                "filled_quantity": "1.00",
                "average_fill_price": "0.0900",
                "fee_paid": "0.0100",
                "client_order_id": "g",
            },
            "kalshi": {"status": "REJECTED", "filled_quantity": "0"},
            "flatten": {
                "status": "FILLED",
                "filled_quantity": "1.00",
                "average_fill_price": "0.0100",
                "fee_paid": "0.0100",
                "client_order_id": "f",
            },
        },
    }


def test_exact_xrp_incident_round_trip_loss_is_ten_cents():
    assert incident.confirmed_flattened_one_leg_pnl(_incident_record()) == D("-0.100000")


def test_not_realized_if_flatten_quantity_does_not_match_open():
    record = _incident_record()
    record["result"]["flatten"]["filled_quantity"] = "0.50"
    assert incident.confirmed_flattened_one_leg_pnl(record) is None


def test_not_realized_if_kalshi_filled_any_quantity():
    record = _incident_record()
    record["result"]["kalshi"]["filled_quantity"] = "1"
    assert incident.confirmed_flattened_one_leg_pnl(record) is None


@pytest.mark.asyncio
async def test_wrapper_records_flatten_and_realized_then_preserves_hard_halt(monkeypatch):
    record = _incident_record()
    posted = []

    async def original(_http, _run_id, _record):
        raise RuntimeError("execution did not finish BOTH_FILLED")

    async def post(_http, event):
        posted.append(event)

    monkeypatch.setattr(incident, "_ORIGINAL_EMIT_EXECUTION_EVENTS", original)
    monkeypatch.setattr(incident.base, "_post_event", post)

    with pytest.raises(RuntimeError, match="BOTH_FILLED"):
        await incident.emit_execution_events_with_incident_accounting(
            object(), "gkp-260820-0816-xrp-02", record
        )

    assert [event["event_type"] for event in posted] == ["FILL", "TRADE_REALIZED"]
    assert posted[0]["side"] == "Gemini EMERGENCY_FLATTEN"
    assert posted[0]["metadata"]["position_flat_after_event"] is True
    assert posted[1]["status"] == "REALIZED_FLATTENED_ONE_LEG"
    assert D(posted[1]["net_pnl"]) == D("-0.100000")
    assert posted[1]["metadata"]["flatten_confirmed"] is True
