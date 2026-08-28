from src.mlb_prop_rule_evidence import build_exact_rule_evidence_packet


def test_complete_packet_preserves_exact_public_rule_payloads_but_never_promotes():
    row = build_exact_rule_evidence_packet(
        family="baseball_player_hits",
        polymarket={
            "id": "astatc-mlb-phi-nym-2026-08-18-hits-example-gte1",
            "metadata": {"instrument_rules": "Player records at least one hit in the named MLB game."},
        },
        kalshi={
            "ticker": "KXMLBHIT-26AUG181900PHINYM-EXAMPLE-1",
            "rules_primary": "Resolves Yes if the named player records at least one hit.",
            "rules_secondary": "Participation and postponement provisions apply.",
            "settlement_sources": ["MLB official statistics"],
        },
    )
    assert row["evidence_complete_for_independent_compare"] is True
    assert row["missing_evidence"] == []
    assert row["polymarket_instrument_id"].startswith("astatc-")
    assert "Participation" in row["kalshi_market_rules"]
    assert row["equivalent"] is False
    assert row["full_rules_audited_shadow"] is False
    assert row["live_probe_ready"] is False
    assert row["read_only"] is True
    assert row["order_entry"] is False


def test_missing_exact_rules_or_sources_fails_closed():
    row = build_exact_rule_evidence_packet(
        family="baseball_player_total_bases",
        polymarket={"slug": "astatc-mlb-example"},
        kalshi={"ticker": "KXMLBTB-EXAMPLE", "rules_primary": "Example exact rule."},
    )
    assert row["evidence_complete_for_independent_compare"] is False
    assert "polymarket_instrument_rules" in row["missing_evidence"]
    assert "kalshi_settlement_sources" in row["missing_evidence"]
    assert row["full_rules_audited_shadow"] is False
    assert row["live_probe_ready"] is False


def test_polymarket_metadata_and_single_string_source_are_supported():
    row = build_exact_rule_evidence_packet(
        family="baseball_player_strikeouts",
        polymarket={
            "metadata": {
                "cftc_instrument_id": "instrument-123",
                "instrument_rules": "Exact instrument-specific rules.",
            }
        },
        kalshi={
            "ticker": "KXMLBKS-EXAMPLE",
            "market_rules": "Exact Kalshi market-specific rules.",
            "settlement_source": "Official league statistics",
        },
    )
    assert row["evidence_complete_for_independent_compare"] is True
    assert row["polymarket_instrument_id"] == "instrument-123"
    assert row["kalshi_settlement_sources"] == ["Official league statistics"]
