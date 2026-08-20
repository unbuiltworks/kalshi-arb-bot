from src.nfl_rule_certifications import (
    GK_MONEYLINE,
    GK_SPREAD,
    GK_TOTAL,
    NflRuleStatus,
    assert_no_unintended_executable,
    certification,
)


def test_no_nfl_family_is_executable_from_identity_overlap_alone():
    assert_no_unintended_executable()
    assert not GK_MONEYLINE.executable
    assert not GK_SPREAD.executable
    assert not GK_TOTAL.executable


def test_moneyline_carries_tie_and_reschedule_blockers():
    row = certification("kalshi", "gemini", "moneyline")
    assert row.status is NflRuleStatus.CONDITIONAL
    assert any("tie_payout_mismatch" in blocker for blocker in row.blockers)
    assert any("postponement_window_mismatch" in blocker for blocker in row.blockers)


def test_spread_matching_overtime_is_not_enough_to_clear_lifecycle():
    row = certification("gemini", "kalshi", "spread")
    assert "gemini_full_contest_includes_overtime" in row.matching_proofs
    assert any("abandonment" in blocker for blocker in row.blockers)
    assert not row.executable


def test_total_terminal_lane_remains_research_only():
    row = certification("gemini", "kalshi", "game_total")
    assert row.conditional_lane == "TERMINAL_OVER_RESEARCH_ONLY"
    assert "terminal_over_state_not_yet_machine_certified" in row.blockers
    assert not row.executable


def test_unknown_pair_or_family_is_unaudited():
    row = certification("fanduel", "gemini", "moneyline")
    assert row.status is NflRuleStatus.UNAUDITED
    assert row.blockers == ("no_complete_nfl_rule_fingerprint",)
