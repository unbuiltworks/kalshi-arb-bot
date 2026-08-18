from decimal import Decimal

from src import gemini_kalshi_opportunity_settlement_audit_shadow as audit

D = Decimal


def test_compatible_settlement_preserves_fee_inclusive_locked_profit():
    result = audit.hypothetical_settlement(
        direction="GEMINI_YES_KALSHI_NO",
        quantity=D("1"),
        entry_capital=D("0.94"),
        gemini_outcome="NO",
        kalshi_outcome="NO",
    )
    assert result["winning_legs"] == 1
    assert result["gross_settlement_payout"] == "1"
    assert result["hypothetical_fee_inclusive_pnl"] == "0.06"
    assert result["venue_outcomes_agree"] is True
    assert result["settlement_pattern"] == "ONE_WIN_ONE_LOSS_COMPATIBLE"


def test_index_mismatch_can_make_both_legs_lose():
    result = audit.hypothetical_settlement(
        direction="GEMINI_YES_KALSHI_NO",
        quantity=D("1"),
        entry_capital=D("0.94"),
        gemini_outcome="NO",
        kalshi_outcome="YES",
    )
    assert result["winning_legs"] == 0
    assert result["gross_settlement_payout"] == "0"
    assert result["hypothetical_fee_inclusive_pnl"] == "-0.94"
    assert result["venue_outcomes_agree"] is False
    assert result["settlement_pattern"] == "BOTH_LEGS_LOSE_INDEX_MISMATCH"


def test_index_mismatch_can_make_both_legs_win():
    result = audit.hypothetical_settlement(
        direction="GEMINI_YES_KALSHI_NO",
        quantity=D("1"),
        entry_capital=D("0.94"),
        gemini_outcome="YES",
        kalshi_outcome="NO",
    )
    assert result["winning_legs"] == 2
    assert result["gross_settlement_payout"] == "2"
    assert result["hypothetical_fee_inclusive_pnl"] == "1.06"
    assert result["venue_outcomes_agree"] is False
    assert result["settlement_pattern"] == "BOTH_LEGS_WIN_INDEX_MISMATCH"


def test_reverse_direction_purchased_sides():
    result = audit.hypothetical_settlement(
        direction="KALSHI_YES_GEMINI_NO",
        quantity=D("1"),
        entry_capital=D("0.97"),
        gemini_outcome="YES",
        kalshi_outcome="YES",
    )
    assert result["purchased_gemini_side"] == "no"
    assert result["purchased_kalshi_side"] == "yes"
    assert result["winning_legs"] == 1
    assert result["hypothetical_fee_inclusive_pnl"] == "0.03"


def test_pass_cells_records_only_true_matrix_cells():
    cells = audit._pass_cells({
        "2c": {"5bp": True, "10bp": False},
        "3c": {"5bp": True, "10bp": True},
    }, 100)
    assert cells == {"2c_5bp_100ms", "3c_5bp_100ms", "3c_10bp_100ms"}
