"""Three ledgers that never merge, and a total no estimate can enter."""
import pytest

from src import pmus_probe_ledger as pl


def L(**kw):
    base = dict(matched_pair_usd="12.50", residual_usd="-3.20", fees_usd="0.00",
                maker_rebate_credited_usd="0.42",
                liquidity_reward_credited_usd="0.00")
    base.update(kw)
    return pl.three_ledgers(**base)


def test_the_three_ledgers_stay_separate():
    led = L()["ledgers"]
    assert set(led) == {pl.TRADING, pl.MAKER_REBATE, pl.LIQUIDITY_REWARD}
    assert led[pl.TRADING]["usd"] == "9.30"
    assert led[pl.MAKER_REBATE]["usd"] == "0.42"
    assert led[pl.LIQUIDITY_REWARD]["usd"] == "0.00"


def test_a_large_accrued_reward_contributes_nothing():
    """The whole point. $115 earned-but-unpaid must not move the total."""
    led = L(liquidity_reward_accrued_usd="115.00")
    assert led["ledgers"][pl.LIQUIDITY_REWARD][
        "accrued_not_credited_usd"] == "115.00"
    assert pl.planning_total(led)["usd"] == "9.72"     # 9.30 + 0.42, not 124.72


def test_credited_liquidity_reward_is_still_excluded_from_planning():
    """Even a REAL credit is excluded from planning economics per the owner's
    rule; it is reported as excluded_usd so the exclusion is visible."""
    led = L(liquidity_reward_credited_usd="50.00")
    t = pl.planning_total(led)
    assert t["usd"] == "9.72"
    assert t["excluded_usd"] == "50.00"


def test_missing_credited_figure_is_unknown_not_zero():
    led = L(liquidity_reward_credited_usd=None)
    lq = led["ledgers"][pl.LIQUIDITY_REWARD]
    assert lq["usd"] is None and lq["resolvable"] is False
    assert "not zero" in lq["why_null"]


def test_an_unresolvable_trading_component_does_not_become_zero():
    led = L(residual_usd=None)
    tr = led["ledgers"][pl.TRADING]
    assert tr["usd"] is None and tr["resolvable"] is False
    assert "residual_usd" in tr["missing"]
    assert pl.planning_total(led)["resolvable"] is False


def test_an_accrued_rebate_may_not_stand_in_for_a_credited_one():
    led = L(maker_rebate_credited_usd=None, maker_rebate_accrued_usd="9.99")
    with pytest.raises(pl.EstimatedRewardRefused):
        pl.planning_total(led)


def test_planning_rule_is_stated_on_every_total():
    for led in (L(), L(liquidity_reward_credited_usd="7.00")):
        assert "until the account actually receives them" in \
            pl.planning_total(led)["rule"]
