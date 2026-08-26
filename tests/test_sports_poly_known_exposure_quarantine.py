from decimal import Decimal

from src.sports_poly_mlb_rule_risk_budget import (
    STRATEGY,
    _blocking_unresolved_for_poly,
    _remaining_risk_capacity,
    _unresolved_risk_used,
)

D = Decimal


def _row(*, trade_id="t1", strategy="sports_gk_autonomous_rule_risk", status="ONE_LEG_FILLED", reserved="0.97", filled="0.44", legs=1, realized=False):
    return {
        "trade_id": trade_id,
        "strategy": strategy,
        "mode": "LIVE_PROBE",
        "status": status,
        "capital_filled": filled,
        "legs_filled": legs,
        "realized": realized,
        "metadata": {"reserved_usd": reserved, "classification": "LIVE_RULE_RISK"},
    }


def test_known_gk_one_leg_is_quarantined_not_global_poly_halt():
    rows = [_row()]
    assert _blocking_unresolved_for_poly(rows) is None
    assert _unresolved_risk_used(rows) == D("0.97")
    loss, unresolved, remaining = _remaining_risk_capacity(rows)
    assert loss == D("0")
    assert unresolved == D("0.97")
    assert remaining == D("4.03")


def test_ambiguous_gk_exposure_still_globally_blocks_poly():
    row = _row(status="AMBIGUOUS_SUBMISSION", legs=0, filled="0", reserved="0.97")
    assert _blocking_unresolved_for_poly([row]) is row


def test_poly_own_unresolved_reservation_blocks_second_poly_maker():
    row = _row(strategy=STRATEGY, status="CAPITAL_RESERVED", legs=0, filled="0", reserved="1")
    assert _blocking_unresolved_for_poly([row]) is row


def test_unknown_nonterminal_other_sports_exposure_stays_fail_closed():
    row = _row(strategy="sports_other_live", status="SOMETHING_NEW", legs=1, filled="0.50", reserved="0.50")
    assert _blocking_unresolved_for_poly([row]) is row


def test_settled_one_leg_no_longer_consumes_unresolved_capacity():
    row = _row(status="SETTLED_ONE_LEG", legs=1, filled="0.44", reserved="0.97", realized=True)
    assert _blocking_unresolved_for_poly([row]) is None
    assert _unresolved_risk_used([row]) == D("0")


def test_unresolved_risk_is_deduped_by_trade_id():
    older = _row(trade_id="same", reserved="0.97", filled="0.44")
    newer = _row(trade_id="same", reserved="0.97", filled="0.44")
    assert _unresolved_risk_used([older, newer]) == D("0.97")
