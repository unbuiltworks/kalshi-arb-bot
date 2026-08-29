from decimal import Decimal as D
import pytest

from src import gemini_liquidity_reward_dynamic_completion_q10 as p


def test_time_dependent_profit_floors():
    assert p.time_dependent_total_profit_floor(180) == D('0.50')
    assert p.time_dependent_total_profit_floor(90) == D('0.25')
    assert p.time_dependent_total_profit_floor(30) == D('0.00')
    with pytest.raises(ValueError):
        p.time_dependent_total_profit_floor(19.9)


def test_exact_probe_can_spend_edge_as_time_runs_down():
    # First live q10 probe: YES filled 48c, NO began at 39c.
    early = p.completion_plan(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.48'), yes_ask=D('0.61'), seconds_to_expiry=180,
    )
    late = p.completion_plan(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.48'), yes_ask=D('0.61'), seconds_to_expiry=45,
    )
    assert early['action'] == 'REPRICE_MAKER_COMPLEMENT'
    assert late['action'] == 'REPRICE_MAKER_COMPLEMENT'
    assert late['target_price'] >= early['target_price']
    assert early['projected_total_profit'] >= D('0.50')
    assert late['projected_total_profit'] >= D('0.00')


def test_never_crosses_maker_ceiling():
    plan = p.completion_plan(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.55'), yes_ask=D('0.60'), seconds_to_expiry=90,
    )
    assert plan['target_price'] <= plan['passive_ceiling']


def test_prices_over_incentive_band_not_used_for_maker_completion():
    ceiling = p.max_profit_safe_complement_price(
        filled_price=D('0.20'), min_total_profit=D('0.00')
    )
    assert ceiling is None or ceiling <= D('0.80')
