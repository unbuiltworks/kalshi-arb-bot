from decimal import Decimal as D

from src.gemini_q10_rescue_policy import (
    Top,
    daily_halt,
    grace_seconds,
    rescue_plan,
    standing_rescue_check,
)


def test_both_flatten_paths_are_priced_and_complete_wins_tie():
    top = Top(D("0.49"), D("20"), D("0.51"), D("20"))
    plan = rescue_plan(filled_outcome="NO", filled_price=D("0.48"), quantity=D("10"), top=top)
    assert {p.kind for p in plan.paths} == {"COMPLETE", "UNWIND"}
    assert plan.best is not None
    assert plan.best.kind == "COMPLETE"
    assert plan.best.depth_ok


def test_standing_guard_checks_both_possible_first_fills():
    top = Top(D("0.49"), D("20"), D("0.51"), D("20"))
    result = standing_rescue_check(
        yes_quote=D("0.48"), no_quote=D("0.48"), quantity=D("10"), top=top,
        max_loss_usd=D("0.30"),
    )
    assert result["safe"] is True
    assert result["yes_first_loss_usd"] <= D("0.30")
    assert result["no_first_loss_usd"] <= D("0.30")


def test_standing_guard_fails_when_rescue_depth_disappears():
    # YES-first requires NO rescue liquidity, which maps to YES-bid depth.
    top = Top(D("0.49"), D("0"), D("0.51"), D("20"))
    result = standing_rescue_check(
        yes_quote=D("0.48"), no_quote=D("0.48"), quantity=D("10"), top=top,
        max_loss_usd=D("0.30"),
    )
    assert result["safe"] is False


def test_standing_guard_fails_when_book_moves_beyond_loss_cap():
    # A 48c NO fill rescued by buying YES at 56c is too expensive once fees are included.
    top = Top(D("0.44"), D("20"), D("0.56"), D("20"))
    result = standing_rescue_check(
        yes_quote=D("0.48"), no_quote=D("0.48"), quantity=D("10"), top=top,
        max_loss_usd=D("0.30"),
    )
    assert result["safe"] is False


def test_toxic_fill_has_zero_grace_but_calm_fill_can_work_maker_briefly():
    assert grace_seconds(toxic=True) == 0.0
    assert grace_seconds(toxic=False) == 2.0


def test_daily_risk_debit_halt():
    assert daily_halt(risk_debits=[D("0.30"), D("0.20")]) is False
    assert daily_halt(risk_debits=[D("1.00"), D("1.00"), D("1.00")]) is True
