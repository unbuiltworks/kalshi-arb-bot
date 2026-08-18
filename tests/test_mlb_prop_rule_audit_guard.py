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
        equivalent=True,
    )


def test_complete_exact_contract_evidence_can_be_full_rules_audited_shadow():
    audit = complete_audit()
    assert audit.complete is True
    row = audit.as_dict()
    assert row["full_rules_audited_shadow"] is True
    assert row["live_probe_ready"] is False
    assert row["read_only"] is True
    assert row["order_entry"] is False


def test_generic_polymarket_reference_without_instrument_rules_fails_closed():
    audit = exact_rule_audit_from_payloads(
        family="baseball_player_hits",
        polymarket={"instrument_id": "some-prop", "rules_ref": "sports-faq"},
        kalshi={
            "ticker": "KXMLBHIT-X",
            "rules": "contract-specific market rules",
            "settlement_sources": ["MLB"],
        },
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        equivalent=True,
    )
    assert audit.complete is False


def test_missing_kalshi_market_specific_rules_fails_closed():
    audit = exact_rule_audit_from_payloads(
        family="baseball_player_hits",
        polymarket={"instrument_id": "some-prop", "instrument_rules": "exact rules"},
        kalshi={"ticker": "KXMLBHIT-X", "settlement_sources": ["MLB"]},
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        equivalent=True,
    )
    assert audit.complete is False


def test_unchecked_cancellation_branch_fails_closed_even_when_summaries_match():
    audit = complete_audit()
    audit = exact_rule_audit_from_payloads(
        family=audit.family,
        polymarket={
            "instrument_id": audit.polymarket_instrument_id,
            "instrument_rules": audit.polymarket_instrument_rules,
        },
        kalshi={
            "ticker": audit.kalshi_ticker,
            "rules": audit.kalshi_market_rules,
            "settlement_sources": list(audit.kalshi_settlement_sources),
        },
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=False,
        source_hierarchy_checked=True,
        equivalent=True,
    )
    assert audit.complete is False


def test_negative_equivalence_never_promotes():
    audit = complete_audit()
    audit = exact_rule_audit_from_payloads(
        family=audit.family,
        polymarket={
            "instrument_id": audit.polymarket_instrument_id,
            "instrument_rules": audit.polymarket_instrument_rules,
        },
        kalshi={
            "ticker": audit.kalshi_ticker,
            "rules": audit.kalshi_market_rules,
            "settlement_sources": list(audit.kalshi_settlement_sources),
        },
        participation_branch_checked=True,
        stat_definition_checked=True,
        cancellation_postponement_checked=True,
        source_hierarchy_checked=True,
        equivalent=False,
    )
    assert audit.complete is False
