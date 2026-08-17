from src.mlb_probe_readiness import classify_probe_row, summarize_probe_rows


def _row(**overrides):
    row = {
        "family": "baseball_player_hits",
        "poly_slug": "poly-market",
        "kalshi_ticker": "KXMLBHIT-EXAMPLE",
        "start_skew_seconds": 0.0,
        "book_comparable": True,
        "participation_gate_satisfied": True,
        "full_rules_audited": True,
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
    out = classify_probe_row(_row(participation_gate_satisfied=False, full_rules_audited=False))
    assert out["preferred_economics"] is True
    assert "PARTICIPATION_PENDING" in out["blockers"]
    assert "RULES_PENDING" in out["blockers"]
    assert out["paper_probe_ready"] is False
    assert out["fully_audited_shadow_ready"] is False


def test_v5_settlement_equivalent_now_means_participation_not_full_audit():
    row = _row()
    row.pop("participation_gate_satisfied")
    row.pop("full_rules_audited")
    row["settlement_equivalent_now"] = True
    row["rule_status"] = "EQUIVALENT_AFTER_PARTICIPATION"
    out = classify_probe_row(row)
    assert out["participation_ok"] is True
    assert out["rules_ok"] is False
    assert out["paper_probe_ready"] is True
    assert out["fully_audited_shadow_ready"] is False
    assert "RULES_PENDING" in out["blockers"]


def test_explicit_full_rules_audited_shadow_status_clears_rule_gate():
    row = _row(full_rules_audited=False, settlement_confidence="FULL_RULES_AUDITED_SHADOW")
    out = classify_probe_row(row)
    assert out["rules_ok"] is True
    assert out["fully_audited_shadow_ready"] is True
    assert out["live_probe_ready"] is False


def test_current_book_and_identity_fail_closed():
    out = classify_probe_row(_row(book_comparable=False, start_skew_seconds=None))
    assert "EXACT_IDENTITY_PENDING" in out["blockers"]
    assert "CURRENT_BOOK_PENDING" in out["blockers"]
    assert out["paper_probe_ready"] is False


def test_summary_never_promotes_live_execution():
    summary = summarize_probe_rows([_row(), _row(full_rules_audited=False)])
    assert summary["preferred_economic_rows"] == 2
    assert summary["paper_probe_ready_rows"] == 2
    assert summary["fully_audited_shadow_ready_rows"] == 1
    assert summary["live_probe_ready_rows"] == 0
    assert summary["read_only"] is True
    assert summary["order_entry"] is False
