from src.mlb_prop_rule_semantic_diff import semantic_diff_from_evidence


def _packet(poly_rules: str, kalshi_rules: str, *, complete: bool = True):
    return {
        "family": "baseball_player_hits",
        "polymarket_instrument_id": "poly-1",
        "polymarket_instrument_rules": poly_rules,
        "kalshi_ticker": "KXMLBHIT-TEST",
        "kalshi_market_rules": kalshi_rules,
        "evidence_complete_for_independent_compare": complete,
    }


def test_semantic_diff_never_promotes_even_when_all_dimensions_present():
    row = semantic_diff_from_evidence(
        _packet(
            "Starter must record a plate appearance. Hits use official MLB stats. "
            "If postponed or cancelled, settlement follows official source rules.",
            "Starting lineup and plate appearance required. Hits use MLB statistics. "
            "Postponed or cancelled games settle using the official source.",
        )
    )
    assert row["semantic_review_ready"] is True
    assert row["equivalent"] is False
    assert row["full_rules_audited_shadow"] is False
    assert row["live_probe_ready"] is False
    assert all(
        dimension["equivalence_decision"] == "REQUIRES_INDEPENDENT_REVIEW"
        for dimension in row["dimensions"].values()
    )


def test_missing_dimension_fails_review_ready_closed():
    row = semantic_diff_from_evidence(
        _packet(
            "Starter must record a plate appearance. Hits use official MLB stats.",
            "Starting lineup and plate appearance required. Hits use MLB statistics.",
        )
    )
    assert row["semantic_review_ready"] is False
    assert "cancellation_postponement" in row["missing_semantic_dimensions"]


def test_incomplete_exact_evidence_cannot_be_review_ready():
    row = semantic_diff_from_evidence(
        _packet(
            "Starter plate appearance; hits; postponed; official MLB source.",
            "Starter plate appearance; hits; postponed; official MLB source.",
            complete=False,
        )
    )
    assert row["semantic_review_ready"] is False
    assert row["full_rules_audited_shadow"] is False


def test_one_sided_anchor_never_counts_as_both():
    row = semantic_diff_from_evidence(
        _packet(
            "Starter plate appearance; hits; postponed; official MLB source.",
            "Hits are used.",
        )
    )
    assert row["dimensions"]["participation"]["evidence_present_both"] is False
    assert row["dimensions"]["cancellation_postponement"]["evidence_present_both"] is False
    assert row["semantic_review_ready"] is False
