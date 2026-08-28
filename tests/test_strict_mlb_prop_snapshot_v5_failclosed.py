from decimal import Decimal

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v5 as v5
from src.mlb_prop_rule_equivalence import ParticipationProof, assess_rule_equivalence


def _candidate():
    return base.StrictCandidate(
        family="baseball_player_hits",
        poly_slug="astatc-mlb-phi-nym-2026-08-17-hits-example-gte1",
        kalshi_ticker="KXMLBHIT-EXAMPLE",
        player="Example Player",
        threshold=Decimal("1"),
        game_date="2026-08-17",
        start_skew_seconds=0.0,
    )


def _cleared_assessment():
    proof = ParticipationProof(
        started=True,
        plate_appearance_recorded=True,
        source="MLB public boxscore",
    )
    return assess_rule_equivalence("baseball_player_hits", proof), proof


def test_participation_cleared_probe_is_paper_not_live():
    assessment, _ = _cleared_assessment()
    # Preferred threshold is >=3 cents per contract, so this q1 +4c plan qualifies.
    probe5 = {"quantity": 1, "conservative_locked_profit": "0.04"}
    positive, preferred = v5._paper_probe_flags(assessment, probe5)
    assert positive is True
    assert preferred is True


def test_empty_row_keeps_legacy_live_fields_fail_closed():
    assessment, proof = _cleared_assessment()
    row = v5._empty(_candidate(), assessment, proof, "book_missing")
    assert row["participation_gate_satisfied"] is True
    assert row["full_rules_audited"] is False
    assert row["rules_status"] == "RULES_PENDING"
    assert row["fully_audited_shadow_ready"] is False
    assert row["positive_live_probe"] is False
    assert row["preferred_live_probe"] is False
    assert row["live_probe_ready"] is False
    assert row["order_entry"] is False
