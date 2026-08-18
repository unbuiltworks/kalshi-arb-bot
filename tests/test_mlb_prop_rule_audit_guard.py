from src.mlb_prop_rule_audit_guard import exact_rule_audit_from_payloads


def complete_audit():
    return exact_rule_audit_from_payloads(
        family="baseball_player_hits",
        polymarket={
            "instrument_id": "astatc-mlb-wsh-nym-2026-08-16-hits-fralin-gte1",
            "instrument_rules": "Named player records at least 1 hit in the specified MLB game; official result governs.",
        },
        kalshi={
            "ticker": "KXMLBHIT-26AUG161340WSHNYM-NYMFLINDOR12-1",
            "rules": "Named starting player records 1+ hits after recording a plate appearance; otherwise fair market price branch applies.",
            "settlement_sources": ["MLB", "ESPN", "Fox Sports"],
        },
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        participation_branch_equivalent=True,
        stat_definition_equivalent=True,
        cancellation_postponement_equivalent=True,
        source_hierarchy_equivalent=True,
        independent_reviewer="independent-rule-review",
        audit_decision_id="mlb-hit-v1",
        equivalent=True,
    )


def test_complete_exact_contract_evidence_can_be_full_rules_audited_shadow():
    audit = complete_audit()
    assert audit.complete is True
    row = audit.as_dict()
    assert row["full_rules_audited_shadow"] is True
    assert row["decision_provenance_complete"] is True
    assert row["live_probe_ready"] is False
    assert row["read_only"] is True
    assert row["order_entry"] is False


def test_global_equivalent_without_dimension_decisions_fails_closed():
    audit = exact_rule_audit_from_payloads(
        family="baseball_player_hits",
        polymarket={"instrument_id": "some-prop", "instrument_rules": "exact rules"},
        kalshi={"ticker": "KXMLBHIT-X", "rules": "exact rules", "settlement_sources": ["MLB"]},
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        independent_reviewer="reviewer",
        audit_decision_id="decision-1",
        equivalent=True,
    )
    assert audit.complete is False


def test_missing_independent_review_provenance_fails_closed():
    audit = exact_rule_audit_from_payloads(
        family="baseball_player_hits",
        polymarket={"instrument_id": "some-prop", "instrument_rules": "exact rules"},
        kalshi={"ticker": "KXMLBHIT-X", "rules": "exact rules", "settlement_sources": ["MLB"]},
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        participation_branch_equivalent=True,
        stat_definition_equivalent=True,
        cancellation_postponement_equivalent=True,
        source_hierarchy_equivalent=True,
        equivalent=True,
    )
    assert audit.complete is False


def test_generic_polymarket_reference_without_instrument_rules_fails_closed():
    audit = exact_rule_audit_from_payloads(
        family="baseball_player_hits",
        polymarket={"instrument_id": "some-prop", "rules_ref": "sports-faq"},
        kalshi={"ticker": "KXMLBHIT-X", "rules": "contract-specific market rules", "settlement_sources": ["MLB"]},
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        participation_branch_equivalent=True,
        stat_definition_equivalent=True,
        cancellation_postponement_equivalent=True,
        source_hierarchy_equivalent=True,
        independent_reviewer="reviewer",
        audit_decision_id="decision-2",
        equivalent=True,
    )
    assert audit.complete is False


def test_unchecked_or_non_equivalent_dimension_fails_closed():
    audit = complete_audit()
    audit = exact_rule_audit_from_payloads(
        family=audit.family,
        polymarket={"instrument_id": audit.polymarket_instrument_id, "instrument_rules": audit.polymarket_instrument_rules},
        kalshi={"ticker": audit.kalshi_ticker, "rules": audit.kalshi_market_rules, "settlement_sources": list(audit.kalshi_settlement_sources)},
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        participation_branch_equivalent=True,
        stat_definition_equivalent=True,
        cancellation_postponement_equivalent=False,
        source_hierarchy_equivalent=True,
        independent_reviewer="reviewer",
        audit_decision_id="decision-3",
        equivalent=True,
    )
    assert audit.complete is False


def test_negative_global_equivalence_never_promotes():
    audit = complete_audit()
    audit = exact_rule_audit_from_payloads(
        family=audit.family,
        polymarket={"instrument_id": audit.polymarket_instrument_id, "instrument_rules": audit.polymarket_instrument_rules},
        kalshi={"ticker": audit.kalshi_ticker, "rules": audit.kalshi_market_rules, "settlement_sources": list(audit.kalshi_settlement_sources)},
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        participation_branch_equivalent=True,
        stat_definition_equivalent=True,
        cancellation_postponement_equivalent=True,
        source_hierarchy_equivalent=True,
        independent_reviewer="reviewer",
        audit_decision_id="decision-4",
        equivalent=False,
    )
    assert audit.complete is False
