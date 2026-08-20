from src.mlb_prop_paper_microprobe import paper_probe_events


def _row() -> dict:
    return {
        "family": "baseball_player_hits",
        "game_date": "2026-08-20",
        "player": "Example Player",
        "threshold": "1",
        "poly_slug": "poly-example",
        "kalshi_ticker": "KXMLBHIT-EXAMPLE",
        "book_comparable": True,
        "synchronized_book_pair": True,
        "quote_receive_skew_ms": 10,
        "fetch_error": None,
        "required_participation_gate": "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE",
        "participation_gate_satisfied": True,
        "participation_proof": {"satisfied": True},
        "rule_status": "EQUIVALENT_AFTER_PARTICIPATION",
        "rules_status": "RULES_PENDING",
        "full_rules_audited": False,
        "fully_audited_shadow_ready": False,
        "execution_eligible": False,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
        "probe_1usd": None,
        "probe_5usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": 2,
            "poly_price": "0.45",
            "kalshi_price": "0.51",
            "poly_fee": "0.01",
            "kalshi_fee_upper_bound": "0.02",
            "total_cost_upper_bound": "1.95",
            "capital_cap": "5",
            "conservative_locked_profit": "0.05",
        },
    }


def test_string_false_does_not_satisfy_book_comparable_gate():
    row = _row()
    row["book_comparable"] = "false"
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None


def test_string_false_does_not_satisfy_participation_gate():
    row = _row()
    row["participation_gate_satisfied"] = "false"
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None


def test_string_rule_flags_do_not_promote_audit_metadata():
    row = _row()
    row["full_rules_audited"] = "true"
    row["fully_audited_shadow_ready"] = "true"
    built = paper_probe_events(row, probe_field="probe_5usd", ts_ms=1)
    assert built is not None
    _events, audit = built
    assert audit["full_rules_audited"] is False
    assert audit["fully_audited_shadow_ready"] is False
    assert audit["live_probe_ready"] is False
