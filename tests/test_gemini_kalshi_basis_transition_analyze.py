from src.gemini_kalshi_basis_transition_analyze import analyze_transition, summarize


def _row(expiry, *, g_strike, k_target, g_final, k_final, g_out="yes", k_out="yes"):
    gap = float(k_target) - float(g_strike)
    safe = "GEMINI_YES_KALSHI_NO" if gap > 0 else "KALSHI_YES_GEMINI_NO"
    return {
        "asset": "BTC",
        "expiry_utc": expiry,
        "gemini_ticker": f"G-{expiry}",
        "kalshi_ticker": f"K-{expiry}",
        "gemini_strike": str(g_strike),
        "kalshi_target": str(k_target),
        "gemini_settlement": str(g_final),
        "kalshi_expiration_value": str(k_final),
        "gemini_outcome": g_out,
        "kalshi_outcome": k_out,
        "dominance_safe_direction_shared_value": safe,
    }


def test_next_gap_inherits_prior_terminal_basis():
    prior = _row(
        "2026-08-22T05:15:00+00:00",
        g_strike=100, k_target=100.01, g_final=90, k_final=91,
        g_out="no", k_out="no",
    )
    current = _row(
        "2026-08-22T05:30:00+00:00",
        g_strike=90, k_target=91, g_final=90.5, k_final=90.6,
        g_out="yes", k_out="no",
    )
    out = analyze_transition(prior, current)
    assert out["evidence_complete"] is True
    assert out["inheritance_residual_value"] == "0"
    assert out["inheritance_sign_matches"] is True
    assert out["current_basis_same_sign_as_gap"] is True
    assert float(out["current_adverse_basis_to_gap_ratio"]) == 0.1
    assert out["current_basis_strictly_overshoots_gap"] is False
    assert out["actual_dominance_breach"] is False


def test_same_sign_growth_beyond_gap_is_basis_overshoot_and_can_breach():
    prior = _row(
        "2026-08-22T05:15:00+00:00",
        g_strike=100, k_target=100.1, g_final=100, k_final=101,
    )
    current = _row(
        "2026-08-22T05:30:00+00:00",
        g_strike=100, k_target=101, g_final=99.9, k_final=101.1,
        g_out="no", k_out="yes",
    )
    out = analyze_transition(prior, current)
    assert out["current_basis_same_sign_as_gap"] is True
    assert float(out["current_adverse_basis_to_gap_ratio"]) > 1
    assert out["current_basis_strictly_overshoots_gap"] is True
    assert out["actual_dominance_breach"] is True


def test_opposite_sign_basis_is_favorable_not_adverse_overshoot():
    prior = _row(
        "2026-08-22T05:15:00+00:00",
        g_strike=100, k_target=100.1, g_final=100, k_final=101,
    )
    current = _row(
        "2026-08-22T05:30:00+00:00",
        g_strike=100, k_target=101, g_final=100.5, k_final=100.4,
        g_out="yes", k_out="no",
    )
    out = analyze_transition(prior, current)
    assert out["current_basis_same_sign_as_gap"] is False
    assert float(out["current_adverse_basis_to_gap_ratio"]) == 0
    assert out["current_basis_strictly_overshoots_gap"] is False
    assert out["actual_dominance_breach"] is False


def test_summary_never_authorizes_trade():
    prior = _row(
        "2026-08-22T05:15:00+00:00",
        g_strike=100, k_target=100.1, g_final=100, k_final=101,
    )
    current = _row(
        "2026-08-22T05:30:00+00:00",
        g_strike=100, k_target=101, g_final=100.5, k_final=100.4,
        g_out="yes", k_out="no",
    )
    out = summarize([prior, current])
    assert out["complete_consecutive_15m_transitions"] == 1
    assert out["actual_dominance_breaches"] == 0
    assert out["trade_authorized"] is False
    assert out["order_entry"] is False
