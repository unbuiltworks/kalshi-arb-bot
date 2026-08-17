from decimal import Decimal

from src.mlb_prop_paper_microprobe import (
    build_paper_probes,
    build_paper_probes_with_state,
    paper_probe_events,
)


def _row(
    profit="0.04",
    *,
    direction="POLY_YES_KALSHI_NO",
    poly_slug="poly-example",
    kalshi_ticker="KXMLBHIT-EXAMPLE",
    participation_gate_satisfied=True,
    settlement_equivalent_now=True,
):
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
        "poly_slug": poly_slug,
        "kalshi_ticker": kalshi_ticker,
        "book_comparable": True,
        "fetch_error": None,
        "required_participation_gate": "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE",
        "participation_proof": {"satisfied": participation_gate_satisfied},
        "rule_status": "EQUIVALENT_AFTER_PARTICIPATION" if participation_gate_satisfied else "CONDITIONAL_PARTICIPATION",
        "rules_status": "RULES_PENDING",
        "participation_gate_satisfied": participation_gate_satisfied,
        # Historical compatibility field must not control paper qualification.
        "settlement_equivalent_now": settlement_equivalent_now,
        "full_rules_audited": False,
        "fully_audited_shadow_ready": False,
        "execution_eligible": False,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
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
    assert audit["poly_slug"] == "poly-example"
    assert audit["kalshi_ticker"] == "KXMLBHIT-EXAMPLE"
    assert audit["participation_gate_satisfied"] is True
    assert audit["rules_status"] == "RULES_PENDING"
    assert audit["full_rules_audited"] is False
    assert audit["fully_audited_shadow_ready"] is False
    assert audit["paper_probe_only"] is True
    assert "settlement_equivalent_now" not in audit
    assert audit["live_probe_ready"] is False
    assert "INDEPENDENT_SETTLEMENT_AUDIT_REQUIRED" in audit["live_blockers"]
    assert "FRESH_EXPLICIT_APPROVAL_REQUIRED" in audit["live_blockers"]


def test_paper_probe_requires_three_cent_minimum_by_default():
    assert paper_probe_events(_row("0.029"), probe_field="probe_5usd", ts_ms=1) is None
    assert paper_probe_events(_row("0.03"), probe_field="probe_5usd", ts_ms=1) is not None


def test_paper_probe_requires_explicit_participation_gate_not_legacy_equivalence_field():
    assert paper_probe_events(
        _row(participation_gate_satisfied=False, settlement_equivalent_now=True),
        probe_field="probe_5usd",
        ts_ms=1,
    ) is None
    # Conversely, paper qualification follows the explicit participation field;
    # the historical compatibility field no longer controls this paper-only path.
    assert paper_probe_events(
        _row(participation_gate_satisfied=True, settlement_equivalent_now=False),
        probe_field="probe_5usd",
        ts_ms=1,
    ) is not None


def test_paper_probe_fails_closed_if_read_only_safety_fields_are_missing_or_unsafe():
    row = _row()
    row.pop("read_only")
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None

    row = _row()
    row["order_entry"] = True
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None

    row = _row()
    row["execution_eligible"] = True
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None

    row = _row()
    row["live_probe_ready"] = True
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None


def test_direction_maps_complementary_sides_correctly():
    built = paper_probe_events(
        _row(direction="KALSHI_YES_POLY_NO"), probe_field="probe_5usd", ts_ms=7
    )
    assert built is not None
    events, _ = built
    fills = {event.venue: event for event in events if event.event_type == "fill"}
    assert fills["kalshi"].side == "YES"
    assert fills["polymarket_us"].side == "NO"


def test_builder_dedupes_same_exact_identity_per_cap():
    row = _row()
    snapshot = {"paper_probe_candidates": [row, dict(row)]}
    events, audits = build_paper_probes(snapshot, ts_ms=9)
    assert len(audits) == 1
    assert len([event for event in events if event.event_type == "fill"]) == 2
    assert Decimal(audits[0]["modeled_profit"]) >= Decimal("0.03")


def test_builder_keeps_same_player_same_day_doubleheader_markets_distinct():
    first = _row(poly_slug="poly-game-1", kalshi_ticker="KXMLBHIT-GAME1")
    second = _row(poly_slug="poly-game-2", kalshi_ticker="KXMLBHIT-GAME2")
    snapshot = {"paper_probe_candidates": [first, second]}
    events, audits = build_paper_probes(snapshot, ts_ms=10)
    assert len(audits) == 2
    assert len({row["settlement_key"] for row in audits}) == 2
    assert len({row["trade_group_id"] for row in audits}) == 2
    assert len([event for event in events if event.event_type == "fill"]) == 4


def test_paper_probe_rejects_missing_venue_identity():
    row = _row()
    row["poly_slug"] = ""
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None


def test_stateful_builder_suppresses_unchanged_resting_depth():
    snapshot = {"paper_probe_candidates": [_row()]}
    first_events, first_audits, state, suppressed = build_paper_probes_with_state(
        snapshot, prior_state={}, ts_ms=100
    )
    assert len(first_audits) == 1
    assert first_audits[0]["episode_event"] == "OPEN"
    assert len(first_events) >= 2
    assert suppressed == 0

    second_events, second_audits, second_state, suppressed = build_paper_probes_with_state(
        snapshot, prior_state=state, ts_ms=200
    )
    assert second_events == []
    assert second_audits == []
    assert second_state == state
    assert suppressed == 1


def test_stateful_builder_emits_move_when_consumable_plan_changes():
    first = _row()
    snapshot = {"paper_probe_candidates": [first]}
    _events, _audits, state, _suppressed = build_paper_probes_with_state(
        snapshot, prior_state={}, ts_ms=100
    )

    moved = _row(profit="0.05")
    moved["probe_5usd"] = dict(moved["probe_5usd"])
    moved["probe_5usd"]["poly_price"] = "0.44"
    events, audits, new_state, suppressed = build_paper_probes_with_state(
        {"paper_probe_candidates": [moved]}, prior_state=state, ts_ms=200
    )
    assert len(events) >= 2
    assert len(audits) == 1
    assert audits[0]["episode_event"] == "MOVE"
    assert new_state != state
    assert suppressed == 0


def test_stateful_builder_clears_absent_opportunity_so_same_quote_can_reopen():
    snapshot = {"paper_probe_candidates": [_row()]}
    _events, _audits, state, _suppressed = build_paper_probes_with_state(
        snapshot, prior_state={}, ts_ms=100
    )
    _events, _audits, closed_state, _suppressed = build_paper_probes_with_state(
        {"paper_probe_candidates": []}, prior_state=state, ts_ms=200
    )
    assert closed_state == {}

    events, audits, _reopened_state, suppressed = build_paper_probes_with_state(
        snapshot, prior_state=closed_state, ts_ms=300
    )
    assert len(events) >= 2
    assert len(audits) == 1
    assert audits[0]["episode_event"] == "OPEN"
    assert suppressed == 0
