from src.mlb_probe_readiness import classify_probe_row, summarize_probe_rows
from src.mlb_prop_rule_audit_guard import AUDIT_CONTRACT


def _exact_audit(**overrides):
    audit = {
        "audit_contract": AUDIT_CONTRACT,
        "family": "baseball_player_hits",
        "polymarket_instrument_id": "poly-instrument-1",
        "kalshi_ticker": "KXMLBHIT-EXAMPLE",
        "participation_branch_checked": True,
        "stat_definition_checked": True,
        "cancellation_postponement_checked": True,
        "source_hierarchy_checked": True,
        "equivalent": True,
        "full_rules_audited_shadow": True,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }
    audit.update(overrides)
    return audit


def _row(**overrides):
    row = {
        "family": "baseball_player_hits",
        "poly_slug": "poly-market",
        "kalshi_ticker": "KXMLBHIT-EXAMPLE",
        "start_skew_seconds": 0.0,
        "book_comparable": True,
        "participation_gate_satisfied": True,
        "exact_rule_audit": _exact_audit(),
        "probe_1usd": {"conservative_locked_profit": "0.02"},
        "probe_5usd": {"conservative_locked_profit": "0.04"},
    }
    row.update(overrides)
    return row


def test_prefers_best_capped_plan_and_requires_three_cents():
    out = classify_probe_row(_row())
    assert out["best_capped_conservative_net"] == "0.04"
    assert out["preferred_economics"] is True
    assert out["paper_probe_ready"] is True
    assert out["fully_audited_shadow_ready"] is True
    assert out["live_probe_ready"] is False


def test_below_three_cents_remains_blocked():
    row = _row(
        probe_1usd={"conservative_locked_profit": "0.01"},
        probe_5usd={"conservative_locked_profit": "0.029"},
    )
    out = classify_probe_row(row)
    assert out["preferred_economics"] is False
    assert "BELOW_PREFERRED_3C_NET" in out["blockers"]
    assert out["paper_probe_ready"] is False


def test_participation_and_rules_are_independent_hard_gates():
    out = classify_probe_row(_row(participation_gate_satisfied=False, exact_rule_audit=None))
    assert out["preferred_economics"] is True
    assert "PARTICIPATION_PENDING" in out["blockers"]
    assert "RULES_PENDING" in out["blockers"]
    assert out["paper_probe_ready"] is False
    assert out["fully_audited_shadow_ready"] is False


def test_v5_settlement_equivalent_now_means_participation_not_full_audit():
    row = _row(exact_rule_audit=None)
    row.pop("participation_gate_satisfied")
    row["settlement_equivalent_now"] = True
    row["rule_status"] = "EQUIVALENT_AFTER_PARTICIPATION"
    out = classify_probe_row(row)
    assert out["participation_ok"] is True
    assert out["rules_ok"] is False
    assert out["paper_probe_ready"] is True
    assert out["fully_audited_shadow_ready"] is False
    assert "RULES_PENDING" in out["blockers"]


def test_generic_full_rules_boolean_cannot_clear_rule_gate():
    row = _row(exact_rule_audit=None, full_rules_audited=True)
    out = classify_probe_row(row)
    assert out["rules_ok"] is False
    assert out["fully_audited_shadow_ready"] is False
    assert "RULES_PENDING" in out["blockers"]


def test_generic_full_rules_status_cannot_clear_rule_gate():
    row = _row(exact_rule_audit=None, settlement_confidence="FULL_RULES_AUDITED_SHADOW")
    out = classify_probe_row(row)
    assert out["rules_ok"] is False
    assert out["fully_audited_shadow_ready"] is False


def test_exact_audit_identity_mismatch_fails_closed():
    row = _row(exact_rule_audit=_exact_audit(kalshi_ticker="KXMLBHIT-WRONG"))
    out = classify_probe_row(row)
    assert out["rules_ok"] is False
    assert out["fully_audited_shadow_ready"] is False


def test_incomplete_exact_audit_branch_fails_closed():
    row = _row(exact_rule_audit=_exact_audit(cancellation_postponement_checked=False))
    out = classify_probe_row(row)
    assert out["rules_ok"] is False
    assert out["fully_audited_shadow_ready"] is False


def test_current_book_and_identity_fail_closed():
    out = classify_probe_row(_row(book_comparable=False, start_skew_seconds=None))
    assert "EXACT_IDENTITY_PENDING" in out["blockers"]
    assert "CURRENT_BOOK_PENDING" in out["blockers"]
    assert out["paper_probe_ready"] is False


def test_summary_never_promotes_live_execution():
    summary = summarize_probe_rows([_row(), _row(exact_rule_audit=None)])
    assert summary["preferred_economic_rows"] == 2
    assert summary["paper_probe_ready_rows"] == 2
    assert summary["fully_audited_shadow_ready_rows"] == 1
    assert summary["live_probe_ready_rows"] == 0
    assert summary["read_only"] is True
    assert summary["order_entry"] is False
