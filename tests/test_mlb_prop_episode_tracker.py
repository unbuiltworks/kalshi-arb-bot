from decimal import Decimal

from src.mlb_prop_episode_tracker import advance_episodes, summarize_events


def _row(profit="0.04", qty=4, px="0.45"):
    return {
        "family": "baseball_player_hits",
        "game_date": "2026-08-17",
        "player": "Example Player",
        "threshold": "1",
        "poly_slug": "poly-game-1-hits-example-gte1",
        "kalshi_ticker": "KXMLBHIT-26AUG171900AAA-BBBPLAYER-1",
        "book_comparable": True,
        # Legacy compatibility field is deliberately not the paper qualification source.
        "settlement_equivalent_now": True,
        "participation_gate_satisfied": True,
        "full_rules_audited": False,
        "rules_status": "RULES_PENDING",
        "fully_audited_shadow_ready": False,
        "live_probe_ready": False,
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
        "fetch_error": None,
        "quote_age_ms": 120,
        "quote_skew_ms": 35,
        "probe_1usd": None,
        "probe_5usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": qty,
            "poly_price": px,
            "kalshi_price": "0.50",
            "poly_fee": "0.01",
            "kalshi_fee_upper_bound": "0.01",
            "total_cost_upper_bound": str(Decimal(qty) - Decimal(profit)),
            "conservative_locked_profit": profit,
        },
    }


def test_open_resting_move_close_lifecycle_without_double_count():
    snap = {"paper_probe_candidates": [_row()]}
    events, state = advance_episodes(snap, prior_state={}, observed_ms=1000)
    assert [e["event"] for e in events] == ["OPEN"]
    assert events[0]["quote_skew_ms"] == 35
    assert events[0]["paper_probe_only"] is True
    assert events[0]["live_probe_ready"] is False
    assert events[0]["rules_status"] == "RULES_PENDING"

    events2, state2 = advance_episodes(snap, prior_state=state, observed_ms=1200)
    assert events2 == []

    moved = {"paper_probe_candidates": [_row(profit="0.05", qty=5)]}
    events3, state3 = advance_episodes(moved, prior_state=state2, observed_ms=1400)
    assert [e["event"] for e in events3] == ["MOVE"]
    assert events3[0]["duration_ms"] == 400

    events4, state4 = advance_episodes({"paper_probe_candidates": []}, prior_state=state3, observed_ms=1800)
    assert state4 == {}
    assert [e["event"] for e in events4] == ["CLOSE"]
    assert events4[0]["duration_ms"] == 400
    summary = summarize_events(events4)
    assert summary["close"] == 1
    assert summary["paper_probe_only"] is True
    assert summary["live_probe_ready"] is False


def test_below_three_cent_plan_never_opens():
    snap = {"paper_probe_candidates": [_row(profit="0.02")]}
    events, state = advance_episodes(snap, prior_state={}, observed_ms=1000, min_profit=Decimal("0.03"))
    assert events == []
    assert state == {}


def test_fail_closed_without_explicit_participation_gate_even_if_legacy_field_true():
    row = _row()
    row["participation_gate_satisfied"] = False
    row["settlement_equivalent_now"] = True
    events, state = advance_episodes({"paper_probe_candidates": [row]}, prior_state={}, observed_ms=1000)
    assert events == []
    assert state == {}


def test_legacy_field_false_does_not_override_explicit_participation_gate():
    row = _row()
    row["settlement_equivalent_now"] = False
    events, state = advance_episodes({"paper_probe_candidates": [row]}, prior_state={}, observed_ms=1000)
    assert [e["event"] for e in events] == ["OPEN"]
    assert state


def test_fail_closed_if_input_claims_live_readiness_or_execution_eligibility():
    for field in ("live_probe_ready", "execution_eligible"):
        row = _row()
        row[field] = True
        events, state = advance_episodes({"paper_probe_candidates": [row]}, prior_state={}, observed_ms=1000)
        assert events == []
        assert state == {}


def test_fail_closed_without_explicit_read_only_contract():
    row = _row()
    row.pop("read_only")
    events, state = advance_episodes({"paper_probe_candidates": [row]}, prior_state={}, observed_ms=1000)
    assert events == []
    assert state == {}
