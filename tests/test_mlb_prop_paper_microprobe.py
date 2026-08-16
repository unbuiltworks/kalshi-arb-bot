from decimal import Decimal

from src.mlb_prop_paper_microprobe import build_paper_probes, paper_probe_events


def _row(profit="0.04", *, direction="POLY_YES_KALSHI_NO"):
    plan = {
        "direction": direction,
        "quantity": 2,
        "poly_price": "0.45",
        "kalshi_price": "0.51",
        "poly_fee": "0.01",
        "kalshi_fee_upper_bound": "0.02",
        "total_cost_upper_bound": "1.95",
        "capital_cap": "5",
        "conservative_locked_profit": profit,
    }
    return {
        "family": "baseball_player_hits",
        "game_date": "2026-08-16",
        "player": "Example Player",
        "threshold": "2",
        "poly_slug": "poly-example",
        "kalshi_ticker": "KXMLBHIT-EXAMPLE",
        "book_comparable": True,
        "fetch_error": None,
        "required_participation_gate": "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE",
        "rule_status": "EQUIVALENT_AFTER_PARTICIPATION",
        "settlement_equivalent_now": False,
        "probe_1usd": None,
        "probe_5usd": plan,
    }


def test_paper_probe_uses_one_timestamp_and_trade_group_for_both_fills():
    built = paper_probe_events(_row(), probe_field="probe_5usd", ts_ms=123456)
    assert built is not None
    events, audit = built
    fills = [event for event in events if event.event_type == "fill"]
    assert len(fills) == 2
    assert {event.ts_ms for event in fills} == {123456}
    assert len({event.trade_group_id for event in fills}) == 1
    assert {event.side for event in fills} == {"YES", "NO"}
    assert audit["trade_group_id"] == fills[0].trade_group_id
    assert audit["live_probe_ready"] is False
    assert "PARTICIPATION_GATE_REQUIRED" in audit["live_blockers"]
    assert "INDEPENDENT_SETTLEMENT_AUDIT_REQUIRED" in audit["live_blockers"]


def test_paper_probe_requires_three_cent_minimum_by_default():
    assert paper_probe_events(_row("0.029"), probe_field="probe_5usd", ts_ms=1) is None
    assert paper_probe_events(_row("0.03"), probe_field="probe_5usd", ts_ms=1) is not None


def test_direction_maps_complementary_sides_correctly():
    built = paper_probe_events(
        _row(direction="KALSHI_YES_POLY_NO"), probe_field="probe_5usd", ts_ms=7
    )
    assert built is not None
    events, _ = built
    fills = {event.venue: event for event in events if event.event_type == "fill"}
    assert fills["kalshi"].side == "YES"
    assert fills["polymarket_us"].side == "NO"


def test_builder_dedupes_same_identity_per_cap():
    row = _row()
    snapshot = {
        "top_probe_1usd": [],
        "top_probe_5usd": [row, dict(row)],
    }
    events, audits = build_paper_probes(snapshot, ts_ms=9)
    assert len(audits) == 1
    assert len([event for event in events if event.event_type == "fill"]) == 2
    assert Decimal(audits[0]["modeled_profit"]) >= Decimal("0.03")
