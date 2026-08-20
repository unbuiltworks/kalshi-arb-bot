from decimal import Decimal

from src.sports_trade_settlement_reconciler import (
    bought_outcomes,
    gemini_resolution_map,
    gemini_settled_positions_map,
    kalshi_resolution_from_payload,
    normalize_result,
    realized_pnl,
    settlement_compatible,
    settlement_payouts,
)

D = Decimal


def _meta(gemini_outcome="YES", kalshi_side="ask"):
    return {
        "gemini_payload": {"symbol": "GEMINI-TEST", "eventOutcome": gemini_outcome},
        "kalshi_payload": {"ticker": "KALSHI-TEST", "side": kalshi_side},
    }


def test_bought_outcomes_decode_kalshi_yes_space_side():
    assert bought_outcomes(_meta("YES", "ask")) == ("yes", "no")
    assert bought_outcomes(_meta("NO", "bid")) == ("no", "yes")


def test_compatible_settlement_pays_exactly_one_dollar_for_complementary_legs():
    gb, kb = bought_outcomes(_meta("YES", "ask"))
    assert settlement_payouts(gemini_bought=gb, kalshi_bought=kb, gemini_result="yes", kalshi_result="yes") == (D("1"), D("0"))
    assert settlement_compatible("yes", "yes") is True


def test_compatible_no_result_still_pays_exactly_one_dollar():
    gb, kb = bought_outcomes(_meta("YES", "ask"))
    assert settlement_payouts(gemini_bought=gb, kalshi_bought=kb, gemini_result="no", kalshi_result="no") == (D("0"), D("1"))


def test_divergent_settlement_can_make_both_legs_win_or_both_lose():
    gb, kb = bought_outcomes(_meta("YES", "ask"))
    assert settlement_payouts(gemini_bought=gb, kalshi_bought=kb, gemini_result="yes", kalshi_result="no") == (D("1"), D("1"))
    assert settlement_payouts(gemini_bought=gb, kalshi_bought=kb, gemini_result="no", kalshi_result="yes") == (D("0"), D("0"))
    assert settlement_compatible("yes", "no") is False


def test_unknown_or_lfmp_never_becomes_realized_payout():
    assert normalize_result("LFMP") is None
    assert normalize_result("") is None
    assert settlement_payouts(gemini_bought="yes", kalshi_bought="no", gemini_result=None, kalshi_result="yes") is None


def test_realized_pnl_uses_actual_capital_and_recorded_fees():
    gross, net = realized_pnl(capital_filled="0.94", fees="0.01", payouts=(D("1"), D("0")))
    assert gross == D("0.06")
    assert net == D("0.05")


def test_divergent_loss_is_recorded_as_real_loss_not_modeled_edge():
    gross, net = realized_pnl(capital_filled="0.94", fees="0.01", payouts=(D("0"), D("0")))
    assert gross == D("-0.94")
    assert net == D("-0.95")


def test_gemini_resolution_map_only_maps_exact_target_symbols_case_insensitively():
    events = [{"contracts": [
        {"instrumentSymbol": "GEMINI-A", "resolutionSide": "YES"},
        {"instrumentSymbol": "GEMINI-B", "resolutionSide": "NO"},
        {"instrumentSymbol": "GEMINI-C", "resolutionSide": "LFMP"},
    ]}]
    assert gemini_resolution_map(events, {"gemini-a", "GEMINI-C", "MISSING"}) == {
        "gemini-a": "yes", "GEMINI-C": None, "MISSING": None,
    }


def test_gemini_settled_positions_map_uses_exact_symbol_and_resolution_side():
    positions = [
        {"instrumentSymbol": "GEMINI-A", "resolutionSide": "YES", "payout": "1.00"},
        {"instrumentSymbol": "GEMINI-B", "resolutionSide": "NO", "payout": "0"},
        {"instrumentSymbol": "GEMINI-C", "resolutionSide": None, "contractMetadata": {"resolutionSide": "yes"}},
    ]
    assert gemini_settled_positions_map(positions, {"gemini-a", "GEMINI-C", "MISSING"}) == {
        "gemini-a": "yes", "GEMINI-C": "yes", "MISSING": None,
    }


def test_kalshi_resolution_parser_accepts_wrapped_or_flat_market_shape():
    assert kalshi_resolution_from_payload({"market": {"result": "yes"}}) == "yes"
    assert kalshi_resolution_from_payload({"result": "NO"}) == "no"
    assert kalshi_resolution_from_payload({"market": {"result": ""}}) is None
