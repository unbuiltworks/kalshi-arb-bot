from src.mlb_prop_paper_microprobe import paper_probe_events


def _row(*, probe_field="probe_5usd"):
    plan = {
        "direction": "POLY_YES_KALSHI_NO",
        "quantity": 2,
        "poly_price": "0.45",
        "kalshi_price": "0.51",
        "poly_fee": "0.01",
        "kalshi_fee_upper_bound": "0.02",
        "total_cost_upper_bound": "1.95",
        "capital_cap": "5" if probe_field == "probe_5usd" else "1",
        # Primitive economics imply <=$0.05. Reporting $0.04 is conservative.
        "conservative_locked_profit": "0.04",
    }
    row = {
        "family": "baseball_player_hits",
        "game_date": "2026-08-20",
        "player": "Example Player",
        "threshold": "2",
        "poly_slug": "poly-example",
        "kalshi_ticker": "KXMLBHIT-EXAMPLE",
        "book_comparable": True,
        "synchronized_book_pair": True,
        "quote_receive_skew_ms": 25,
        "fetch_error": None,
        "required_participation_gate": "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE",
        "participation_proof": {"satisfied": True},
        "participation_gate_satisfied": True,
        "rule_status": "EQUIVALENT_AFTER_PARTICIPATION",
        "rules_status": "RULES_PENDING",
        "full_rules_audited": False,
        "fully_audited_shadow_ready": False,
        "execution_eligible": False,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
        "probe_1usd": None,
        "probe_5usd": None,
    }
    row[probe_field] = plan
    return row


def test_valid_conservative_plan_is_recomputed_and_kept():
    built = paper_probe_events(_row(), probe_field="probe_5usd", ts_ms=1)
    assert built is not None
    _events, audit = built
    assert audit["paper_plan_economics_validation"] == "RECOMPUTED_PRIMITIVE_COST_AND_CONSERVATIVE_PROFIT_CEILING"
    assert audit["recomputed_primitive_cost"] == "1.95"
    assert audit["reported_total_cost_upper_bound"] == "1.95"
    assert audit["implied_profit_ceiling"] == "0.05"


def test_inflated_reported_profit_cannot_manufacture_three_cent_edge():
    row = _row()
    row["probe_5usd"]["conservative_locked_profit"] = "0.10"
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None


def test_understated_total_cost_is_rejected():
    row = _row()
    row["probe_5usd"]["total_cost_upper_bound"] = "1.90"
    assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None


def test_budget_cap_is_rechecked_downstream():
    row = _row(probe_field="probe_1usd")
    # Same producer plan costs $1.95, so it cannot be a <=$1 synthetic probe.
    assert paper_probe_events(row, probe_field="probe_1usd", ts_ms=1) is None


def test_nonfinite_or_structurally_invalid_economics_fail_closed():
    for field, value in (
        ("conservative_locked_profit", "NaN"),
        ("poly_fee", "-0.01"),
        ("quantity", "1.5"),
        ("poly_price", "1.1"),
    ):
        row = _row()
        row["probe_5usd"][field] = value
        assert paper_probe_events(row, probe_field="probe_5usd", ts_ms=1) is None
